"""Resumable native MR platform capture with audited, platform-only local backfill."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import urlsplit

from backend.analytics.hk_platform_output_report import _run_team, _team_matches


def _text(value):
    return str(value or "").strip()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


def _observed_at(value):
    text = _text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Native observation timestamp is required") from error
    if parsed.tzinfo is None:
        raise ValueError("Native observation timestamp must include a timezone")
    return text


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def build_plan(db_path, *, batch_size=200, team_name="DTSV_China"):
    if not 1 <= batch_size <= 500:
        raise ValueError("Batch size must be between 1 and 500")
    source = Path(db_path).resolve()
    connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        targets = []
        for row in connection.execute("SELECT mr_id,test_id,team,testplatformid,raw_json FROM octane_manual_runs WHERE TRIM(COALESCE(testplatformid,''))=''"):
            record = dict(row)
            if not _team_matches(_run_team(record)[0], team_name):
                continue
            run_id, test_id = _text(record["mr_id"]), _text(record["test_id"])
            if not run_id.isdigit() or not test_id.isdigit():
                raise ValueError("Missing or nonnumeric local MR/testcase binding: " + run_id)
            targets.append({"mr_id": run_id, "test_id": test_id})
    finally:
        connection.close()
    targets.sort(key=lambda row: int(row["mr_id"]))
    return {"schema_version": "1.0", "database": str(source), "team_name": team_name,
            "created_at": datetime.now(timezone.utc).isoformat(), "plan_sha256": _digest(targets),
            "batch_size": batch_size, "targets": targets,
            "batches": [{"batch_id": f"{index // batch_size:06d}",
                         "ids": [row["mr_id"] for row in targets[index:index + batch_size]]}
                        for index in range(0, len(targets), batch_size)]}


def validate_batch(plan, batch_id, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Native batch records must be an explicit list")
    batch = next((row for row in plan["batches"] if row["batch_id"] == batch_id), None)
    if batch is None:
        raise ValueError("Unexpected batch ID")
    observed_at = _observed_at(payload.get("observed_at"))
    expected = {row["mr_id"]: row["test_id"] for row in plan["targets"]}
    requested = set(batch["ids"])
    seen, records, errors = set(), [], []
    for row in payload.get("records", []):
        if not isinstance(row, dict):
            raise ValueError("Native batch row must be an object")
        run_id = _text(row.get("id"))
        if run_id not in requested or run_id in seen:
            raise ValueError("Unexpected or duplicate native MR ID: " + run_id)
        seen.add(run_id)
        native_test = row.get("test") or {}
        test_id = _text(native_test.get("id")) if isinstance(native_test, dict) else ""
        if test_id != expected[run_id]:
            errors.append({"id": run_id, "reason": "testcase_mismatch", "expected_test_id": expected[run_id], "observed_test_id": test_id})
            continue
        if "testplatformid_udf" not in row:
            raise ValueError("Verified native platform field was not returned: " + run_id)
        value = row["testplatformid_udf"]
        if value is not None and not isinstance(value, str):
            raise ValueError("Native platform identifier must be text or null")
        identifier = _text(value)
        records.append({"id": run_id, "test": {"id": test_id}, "testplatformid_udf": identifier,
                        "last_modified": _text(row.get("last_modified")), "observed_at": observed_at,
                        "capture_state": "native_populated" if identifier else "native_empty"})
    errors += [{"id": run_id, "reason": "not_returned_by_native_query"} for run_id in sorted(requested - seen)]
    return {"plan_sha256": plan["plan_sha256"], "batch_id": batch_id, "observed_at": observed_at,
            "requested_mrs": len(requested), "records": records, "errors": errors}


def apply_platform_snapshot(db_path, snapshot, backup_path):
    records = snapshot.get("records", [])
    observed_at = _observed_at(snapshot.get("fetched_at"))
    records_digest = _digest(records)
    connection = sqlite3.connect(db_path, timeout=60)
    connection.row_factory = sqlite3.Row
    counts = Counter()
    try:
        connection.execute("BEGIN IMMEDIATE")
        before, seen = [], set()
        for record in records:
            run_id = _text(record.get("id"))
            if not run_id or run_id in seen:
                raise ValueError("Missing or duplicate backfill MR ID")
            seen.add(run_id)
            row = connection.execute("SELECT mr_id,test_id,team,testplatformid,raw_json FROM octane_manual_runs WHERE mr_id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Backfill MR does not exist in local source: " + run_id)
            test_id = _text((record.get("test") or {}).get("id"))
            if not test_id or test_id != _text(row["test_id"]):
                raise ValueError("Backfill testcase binding differs: " + run_id)
            if not _team_matches(_run_team(dict(row))[0], snapshot.get("team_name", "DTSV_China")):
                raise ValueError("Backfill MR is outside the authorized team: " + run_id)
            if "testplatformid_udf" not in record:
                raise ValueError("Native platform observation is missing")
            if record["testplatformid_udf"] is not None and not isinstance(record["testplatformid_udf"], str):
                raise ValueError("Native platform observation must be text or null")
            _observed_at(record.get("observed_at") or observed_at)
            before.append({"mr_id": run_id, "test_id": test_id, "testplatformid": row["testplatformid"]})
        if Path(backup_path).exists():
            prior = json.loads(Path(backup_path).read_text(encoding="utf-8"))
            if prior.get("records_sha256") != records_digest:
                raise ValueError("Existing recovery journal belongs to another snapshot")
        else:
            _write_json(backup_path, {"database": str(Path(db_path).resolve()), "records_sha256": records_digest,
                                      "created_at": datetime.now(timezone.utc).isoformat(), "rows": before})
        connection.execute("""CREATE TABLE IF NOT EXISTS octane_mr_platform_observations (
            mr_id TEXT NOT NULL, test_id TEXT NOT NULL, platform_identifier TEXT NOT NULL,
            capture_state TEXT NOT NULL, observed_at TEXT NOT NULL, native_last_modified TEXT,
            records_sha256 TEXT NOT NULL, snapshot_file TEXT,
            PRIMARY KEY (mr_id, observed_at)
        )""")
        for record, original in zip(records, before):
            identifier = _text(record["testplatformid_udf"])
            counts["native_populated" if identifier else "native_empty"] += 1
            current = _text(original["testplatformid"])
            if current:
                counts["already_populated" if current == identifier else "preserved_concurrent_value"] += 1
            elif identifier:
                cursor = connection.execute("UPDATE octane_manual_runs SET testplatformid=? WHERE mr_id=? AND test_id=? AND TRIM(COALESCE(testplatformid,''))=''", (identifier, original["mr_id"], original["test_id"]))
                counts["updated_nonempty"] += cursor.rowcount
            connection.execute("""INSERT OR IGNORE INTO octane_mr_platform_observations
                (mr_id,test_id,platform_identifier,capture_state,observed_at,native_last_modified,records_sha256,snapshot_file)
                VALUES (?,?,?,?,?,?,?,?)""", (original["mr_id"], original["test_id"], identifier,
                "native_populated" if identifier else "native_empty", record.get("observed_at") or observed_at,
                record.get("last_modified", ""), records_digest, snapshot.get("snapshot_file", "")))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"records": len(records), **dict(counts), "recovery_journal": str(backup_path), "records_sha256": records_digest}


class BackfillJob:
    def __init__(self, db_path, output_dir, *, batch_size=200, team_name="DTSV_China"):
        self.output = Path(output_dir).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.plan_path = self.output / "plan.json"
        self.lock = threading.RLock()
        if self.plan_path.exists():
            self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
            if Path(self.plan["database"]).resolve() != Path(db_path).resolve() or self.plan["team_name"] != team_name:
                raise ValueError("Saved backfill plan belongs to another source or team")
            if self.plan["plan_sha256"] != _digest(self.plan["targets"]):
                raise ValueError("Saved plan binding hash does not match")
        else:
            self.plan = build_plan(db_path, batch_size=batch_size, team_name=team_name)
            _write_json(self.plan_path, self.plan)
        self.completed = {}
        for batch in self.plan["batches"]:
            path = self.output / ("batch_" + batch["batch_id"] + ".json")
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("plan_sha256") != self.plan["plan_sha256"]:
                    raise ValueError("Checkpoint belongs to another plan")
                self.completed[batch["batch_id"]] = data

    def pending_batches(self):
        return [row for row in self.plan["batches"] if row["batch_id"] not in self.completed]

    def progress(self):
        counts = Counter()
        for data in self.completed.values():
            counts["processed_mrs"] += data["requested_mrs"]
            counts["errors"] += len(data["errors"])
            counts.update(record["capture_state"] for record in data["records"])
        return {"target_mrs": len(self.plan["targets"]), "total_batches": len(self.plan["batches"]),
                "completed_batches": len(self.completed), **dict(counts)}

    def save_batch(self, batch_id, payload):
        with self.lock:
            if batch_id in self.completed:
                return self.progress()
            data = validate_batch(self.plan, batch_id, payload)
            _write_json(self.output / ("batch_" + batch_id + ".json"), data)
            self.completed[batch_id] = data
            return self.progress()

    def finalize(self, *, apply=False):
        with self.lock:
            if self.pending_batches():
                raise ValueError("Native MR capture is incomplete; resume pending batches first")
            records = [row for batch in self.plan["batches"] for row in self.completed[batch["batch_id"]]["records"]]
            errors = [row for batch in self.plan["batches"] for row in self.completed[batch["batch_id"]]["errors"]]
            path = self.output / "native_mr_platform_links.json"
            observed = [row["observed_at"] for row in self.completed.values()]
            snapshot = {"schema_version": "1.0", "fetched_at": max(observed) if observed else datetime.now(timezone.utc).isoformat(),
                        "snapshot_file": str(path), "team_name": self.plan["team_name"],
                        "source": "Authenticated read-only Octane manual_runs GET by existing local MR IDs",
                        "native_entity": "run_manual", "verified_identifier_field": "testplatformid_udf",
                        "plan_sha256": self.plan["plan_sha256"], "records": records, "errors": errors,
                        "capture_summary": self.progress(), "source_database_updated": False}
            _write_json(path, snapshot)
            if apply:
                result = apply_platform_snapshot(self.plan["database"], snapshot, self.output / "before_platform_values.json")
                snapshot["source_database_updated"] = True
                snapshot["source_backfill_result"] = result
                _write_json(path, snapshot)
            return snapshot


def make_server(job, *, api_path, origin="https://octane-prod.bmwgroup.net", port=0, apply=False):
    parsed_origin = urlsplit(origin)
    if parsed_origin.scheme != "https" or parsed_origin.path not in {"", "/"}:
        raise ValueError("The authorized browser origin must use HTTPS")
    if not re.fullmatch(r"/api/shared_spaces/\d+/workspaces/\d+/manual_runs", api_path):
        raise ValueError("Unexpected native MR read endpoint")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def authorized(self):
            return (self.headers.get("Origin") == origin
                    and not self.headers.get("Authorization") and not self.headers.get("Cookie"))

        def send_json(self, status, payload):
            content = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
            self.wfile.write(content)

        def do_OPTIONS(self):
            self.send_json(204 if self.authorized() else 403, {})

        def do_GET(self):
            if not self.authorized():
                self.send_json(403, {"error": "Origin or authentication-header policy rejected"})
                return
            if self.path == "/plan":
                self.send_json(200, {**job.progress(), "plan_sha256": job.plan["plan_sha256"],
                    "batches": job.pending_batches(), "api_path": api_path,
                    "fields": "id,test{id},testplatformid_udf,last_modified"})
            elif self.path == "/status":
                self.send_json(200, job.progress())
            else:
                self.send_json(404, {"error": "Unknown checkpoint route"})

        def do_POST(self):
            if not self.authorized():
                self.send_json(403, {"error": "Origin or authentication-header policy rejected"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("A bounded JSON checkpoint payload is required")
                payload = json.loads(self.rfile.read(length))
                match = re.fullmatch(r"/batch/(\d{6})", self.path)
                if match:
                    progress = job.save_batch(match[1], payload)
                    print(json.dumps({"checkpoint": match[1], **progress}), flush=True)
                    self.send_json(200, progress)
                elif self.path == "/finish":
                    snapshot = job.finalize(apply=apply)
                    summary = {"snapshot_file": snapshot["snapshot_file"],
                               "source_database_updated": snapshot["source_database_updated"],
                               "source_backfill_result": snapshot.get("source_backfill_result"),
                               **snapshot["capture_summary"]}
                    self.send_json(200, summary)
                    print(json.dumps({"finished": True, **summary}), flush=True)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.send_json(404, {"error": "Unknown checkpoint route"})
            except (ValueError, KeyError) as error:
                self.send_json(409, {"error": str(error)})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    from backend.analytics.config import get_octane_base_url, get_octane_shared_space_id, get_octane_workspace_id

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="database/source/qgate_raw.db")
    parser.add_argument("--output", required=True)
    parser.add_argument("--team-name", default="DTSV_China")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--apply", action="store_true", help="After complete capture, update only missing source platform identifiers with an original-value journal")
    parser.add_argument("--plan-only", action="store_true")
    arguments = parser.parse_args(argv)
    job = BackfillJob(arguments.db, arguments.output, batch_size=arguments.batch_size, team_name=arguments.team_name)
    if arguments.plan_only:
        print(json.dumps({"plan_file": str(job.plan_path), **job.progress()}), flush=True)
        return
    origin = get_octane_base_url().rstrip("/")
    api_path = "/api/shared_spaces/" + get_octane_shared_space_id() + "/workspaces/" + get_octane_workspace_id() + "/manual_runs"
    server = make_server(job, api_path=api_path, origin=origin, port=arguments.port, apply=arguments.apply)
    print(json.dumps({"checkpoint_base": "http://127.0.0.1:" + str(server.server_port),
                      "authorized_origin": origin, "apply_on_complete": arguments.apply,
                      "credentials_accepted": False, **job.progress()}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()