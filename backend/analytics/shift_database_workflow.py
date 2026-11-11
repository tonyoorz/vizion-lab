from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from hashlib import file_digest, sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from backend.analytics.open_ticket_postponement import build_ticket_record, select_open_shift_ticket_records
from backend.analytics.open_shift_scenario_triple import (
    build_merged_shift_tolerate_artifact_triple,
    build_shift_full_records,
    load_open_shift_raw_evidence,
)
from backend.analytics.phase09_wide_postponement_scan import classify_shift_tolerate_process
from backend.analytics.shift_current_evidence import apply_current_evidence, build_current_evidence_input


SNAPSHOT_TABLES = (
    "octane_defects", "octane_defect_history_events",
    "octane_defect_comment_refresh_state", "octane_defect_history_refresh_state",
)
WORKFLOW_VERSION = "shift-database-workflow-v1"
ANALYSIS_FINGERPRINT_POLICY = "database-analysis-evidence-v2"
FETCH_METADATA_FIELDS = {"fetched_at", "refreshed_at", "snapshot_file", "prepared_at", "evidence_refreshed_at", "evidence_fingerprint"}


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _analysis_evidence(value: Any) -> Any:
    if isinstance(value, list):
        return [_analysis_evidence(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {}
    for key, item in value.items():
        if key in FETCH_METADATA_FIELDS:
            continue
        if key.endswith("_json") and isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError:
                pass
        normalized[key] = _analysis_evidence(item)
    return normalized


def _history_rows(connection: sqlite3.Connection, ticket_id: str) -> list[dict[str, Any]]:
    rows = [dict(row) for row in connection.execute("SELECT * FROM octane_defect_history_events WHERE defect_id=?", (ticket_id,))]
    return sorted(rows, key=lambda row: (str(row.get("event_timestamp") or ""), int(row.get("entry_index") or 0), int(row.get("change_index") or 0)))


def _comments(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        comments = json.loads(ticket.get("comments") or "[]")
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"Invalid canonical comments for {ticket['defect_id']}") from error
    if not isinstance(comments, list) or any(not isinstance(item, dict) for item in comments):
        raise ValueError(f"Invalid canonical comments for {ticket['defect_id']}")
    return comments


def _copy_scope(source: sqlite3.Connection, destination: Path, ticket_ids: list[str]) -> None:
    snapshot = sqlite3.connect(destination)
    try:
        for table in SNAPSHOT_TABLES:
            definition = source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if definition is None:
                continue
            snapshot.execute(definition[0])
            for start in range(0, len(ticket_ids), 900):
                batch = ticket_ids[start:start + 900]
                placeholders = ",".join("?" for _ticket_id in batch)
                cursor = source.execute(f"SELECT * FROM {table} WHERE defect_id IN ({placeholders})", batch)
                while rows := cursor.fetchmany(1000):
                    values = ",".join("?" for _value in rows[0])
                    snapshot.executemany(f"INSERT INTO {table} VALUES ({values})", [tuple(row) for row in rows])
            snapshot.execute(f"CREATE INDEX IF NOT EXISTS snapshot_{table}_ticket_idx ON {table}(defect_id)")
        snapshot.commit()
    finally:
        snapshot.close()


def load_database_snapshot_inputs(
    snapshot_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    connection = _read_only(snapshot_path)
    inputs: list[dict[str, Any]] = []
    records: dict[str, dict[str, Any]] = {}
    fetched_at: list[str] = []
    empty_comments: list[str] = []
    consistency_warnings: list[str] = []
    comment_count = 0
    history_count = 0
    try:
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for row in connection.execute("SELECT * FROM octane_defects ORDER BY defect_id"):
            ticket = dict(row)
            ticket_id = str(ticket["defect_id"])
            comments = _comments(ticket)
            history = _history_rows(connection, ticket_id)
            fingerprint = sha256(json.dumps({
                "policy": ANALYSIS_FINGERPRINT_POLICY,
                "ticket": _analysis_evidence(ticket), "history": _analysis_evidence(history),
            }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            if "octane_defect_comment_refresh_state" in tables:
                cached = connection.execute("SELECT comments_json FROM octane_defect_comment_refresh_state WHERE defect_id=?", (ticket_id,)).fetchone()
                if cached and json.loads(cached[0] or "[]") != comments:
                    raise ValueError(f"Canonical/cache comment consistency failed for {ticket_id}")
            if ticket.get("raw_json"):
                raw = json.loads(ticket["raw_json"])
                if "comments" in raw and raw["comments"] != comments:
                    raise ValueError(f"Canonical/raw comment consistency failed for {ticket_id}")
                if "comments" not in raw:
                    consistency_warnings.append(ticket_id)
            else:
                consistency_warnings.append(ticket_id)
            if not comments:
                empty_comments.append(ticket_id)
            source_time = str(ticket.get("fetched_at") or "")
            if source_time:
                fetched_at.append(source_time)
            evidence_ticket = {
                **ticket, "source_mode": "database_snapshot",
                "evidence_refreshed_at": source_time,
                "source_evidence_status": "database_snapshot",
            }
            inputs.append(build_current_evidence_input(evidence_ticket, history, source_fingerprint=fingerprint))
            records[ticket_id] = {
                **evidence_ticket, **build_ticket_record(ticket, history),
                "evidence_fingerprint": fingerprint,
            }
            comment_count += len(comments)
            history_count += len(history)
    finally:
        connection.close()
    audit = {
        "source_mode": "database_snapshot", "requested_ticket_count": len(records),
        "current_verified_ticket_count": 0, "database_snapshot_ticket_count": len(records),
        "database_snapshot_integrity_verified": True,
        "canonical_source_verified": not consistency_warnings,
        "unavailable_ticket_ids": [], "comment_count": comment_count,
        "history_row_count": history_count, "empty_comments_ticket_ids": empty_comments,
        "unknown_raw_comment_provenance_ticket_ids": consistency_warnings,
        "earliest_evidence_refresh": min(fetched_at, default=""),
        "latest_evidence_refresh": max(fetched_at, default=""),
        "live_source_freshness_verified": False,
        "analysis_fingerprint_policy": ANALYSIS_FINGERPRINT_POLICY,
        "fetch_completeness": "Not verified by an offline database snapshot; empty evidence may be unfetched.",
    }
    return inputs, records, audit


def prepare_database_workflow(
    database_path: Path,
    run_directory: Path,
    *,
    project: str,
    created_from: str = "",
) -> dict[str, Any]:
    if not database_path.is_file() or not project.strip():
        raise ValueError("An existing source database and explicit project scope are required")
    if created_from:
        date.fromisoformat(created_from)
    scope = {"project": project.strip(), "created_from": created_from}
    manifest_path = run_directory / "database_workflow_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["scope"] != scope or manifest["source_database"] != str(database_path.resolve()):
            raise ValueError("Existing database workflow belongs to another source scope")
        if manifest.get("source_audit", {}).get("analysis_fingerprint_policy") != ANALYSIS_FINGERPRINT_POLICY:
            raise ValueError("Run uses a previous analysis fingerprint policy; prepare a new run before model work")
        if _hash_file(Path(manifest["snapshot_path"])) != manifest["snapshot_sha256"]:
            raise ValueError("Database snapshot fingerprint changed")
        return manifest
    run_directory.mkdir(parents=True, exist_ok=True)
    snapshot_path = run_directory / "database_evidence_snapshot.sqlite3"
    inputs_path = run_directory / "current_state_llm_inputs.jsonl"
    if snapshot_path.exists() or inputs_path.exists():
        raise ValueError("Incomplete run directory; use a new directory instead of overwriting evidence")
    temporary = run_directory / "database_evidence_snapshot.partial.sqlite3"
    temporary.unlink(missing_ok=True)
    connection = _read_only(database_path)
    selected: dict[str, dict[str, Any]] = {}
    scanned = 0
    try:
        connection.execute("BEGIN")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(octane_defects)")}
        if not {"defect_id", "phase", "creation_time"} <= columns or not ({"tproject", "project"} & columns):
            raise ValueError("Source database lacks ticket/project/date scope columns")
        project_columns = [field for field in ("tproject", "project") if field in columns]
        project_expression = "COALESCE(" + ",".join(f"NULLIF(trim({field}), '')" for field in project_columns) + ", '')"
        query = f"SELECT * FROM octane_defects WHERE lower({project_expression})=lower(?)"
        parameters = [scope["project"]]
        if created_from:
            query += " AND datetime(creation_time)>=datetime(?)"
            parameters.append(created_from)
        query += " ORDER BY defect_id"
        for row in connection.execute(query, parameters):
            scanned += 1
            ticket = dict(row)
            ticket_id = str(ticket["defect_id"])
            history = _history_rows(connection, ticket_id)
            derived = build_ticket_record(ticket, history)
            process = classify_shift_tolerate_process(history_events=history, description=ticket.get("description"), comments=_comments(ticket))
            is_open_shift = str(ticket.get("phase") or "")[:2] not in {"06", "09"} and bool(select_open_shift_ticket_records([derived]))
            is_process = process["process_tier"] != "No process evidence"
            if is_open_shift or is_process:
                selected[ticket_id] = {
                    "selection_scope": "; ".join(label for flag, label in ((is_open_shift, "Open Shift"), (is_process, "Historical Shift/Tolerate process candidate")) if flag),
                    **process,
                }
        if not selected:
            raise ValueError("No Shift/Tolerate candidates matched the explicit database scope")
        _copy_scope(connection, temporary, sorted(selected))
    finally:
        connection.close()
    temporary.replace(snapshot_path)
    inputs, _records, audit = load_database_snapshot_inputs(snapshot_path)
    inputs_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in inputs), encoding="utf-8")
    audit.update({
        "database_scope_ticket_count": scanned,
        "selection_policy": "Existing Open Shift predicates plus historical process candidates across recorded phases; not an approved current postponement decision.",
        "selection_completeness": "Limited to evidence present in this database snapshot; missing source evidence can hide candidates.",
        "phase_counts": dict(Counter(str(record.get("phase") or "") for record in _records.values())),
    })
    manifest = {
        "workflow_version": WORKFLOW_VERSION, "source_mode": "database_snapshot",
        "source_database": str(database_path.resolve()), "scope": scope,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "ticket_ids": sorted(selected), "selection_records": selected,
        "snapshot_path": str(snapshot_path.resolve()), "snapshot_sha256": _hash_file(snapshot_path),
        "inputs_path": str(inputs_path.resolve()), "source_audit": audit,
    }
    (run_directory / "selected_ticket_ids.json").write_text(json.dumps(manifest["ticket_ids"], indent=2), encoding="utf-8")
    (run_directory / "selected_ticket_scope.json").write_text(json.dumps({
        "artifact_type": "ingestion_scope_only", "source_database": str(database_path.resolve()),
        "scope": scope, "ticket_mappings": [{"ticket_id": ticket_id} for ticket_id in manifest["ticket_ids"]],
    }, indent=2), encoding="utf-8")
    (run_directory / "database_source_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_database_workflow(
    run_directory: Path,
    *,
    summaries_path: Path,
    output_directory: Path | None = None,
) -> dict[str, Path]:
    manifest = json.loads((run_directory / "database_workflow_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("workflow_version") != WORKFLOW_VERSION:
        raise ValueError("Unsupported database workflow version")
    if manifest.get("source_audit", {}).get("analysis_fingerprint_policy") != ANALYSIS_FINGERPRINT_POLICY:
        raise ValueError("Run uses a previous analysis fingerprint policy; prepare a new run")
    snapshot = Path(manifest["snapshot_path"])
    if _hash_file(snapshot) != manifest["snapshot_sha256"]:
        raise ValueError("Database snapshot fingerprint changed")
    inputs, current, audit = load_database_snapshot_inputs(snapshot)
    saved_inputs = [json.loads(line) for line in Path(manifest["inputs_path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    if inputs != saved_inputs or sorted(current) != manifest["ticket_ids"]:
        raise ValueError("Prepared database inputs or ticket scope changed; prepare a new run")
    summaries = {
        str(item["ticket_id"]): item
        for line in summaries_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for item in [json.loads(line)]
    }
    selected = [{"ticket_id": ticket_id, **manifest["selection_records"][ticket_id]} for ticket_id in manifest["ticket_ids"]]
    validated = apply_current_evidence(selected, summaries, inputs, current, set())
    classified = {
        str(record["ticket_id"]): record
        for record in build_shift_full_records(list(current.values()), summaries, current)
    }
    records = [{**classified[str(record["ticket_id"])], **record} for record in validated]
    for record in records:
        record["confidence_basis"] = "Selected from recorded database evidence; snapshot analysis does not establish current online coverage or process approval."
    source_audit = {**manifest["source_audit"], **audit}
    raw_evidence = load_open_shift_raw_evidence(snapshot, manifest["ticket_ids"])
    outputs = build_merged_shift_tolerate_artifact_triple(
        records, output_directory=output_directory or run_directory / "candidate_v12",
        source_snapshot=audit["earliest_evidence_refresh"], raw_evidence=raw_evidence,
        source_evidence_audit=source_audit,
    )
    result = {
        "source_mode": "database_snapshot", "source_audit": source_audit,
        "ticket_count": len(records), "raw_evidence_rows": len(raw_evidence),
        "outputs": {key: str(path.resolve()) for key, path in outputs.items()},
    }
    (run_directory / "candidate_build_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return outputs