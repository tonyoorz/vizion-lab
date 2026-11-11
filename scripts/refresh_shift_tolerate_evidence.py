from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.ingest.client import build_default_octane_client
from backend.analytics.ingest.pipeline import refresh_octane_tickets_by_ids
from backend.analytics.config import get_full_picture_source_db_path


def backup_ticket_scope(database_path: Path, ticket_ids: list[str], output: Path) -> None:
    if output.exists():
        existing = sqlite3.connect(output.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if existing.execute("PRAGMA quick_check").fetchone()[0] != "ok" or {str(row[0]) for row in existing.execute("SELECT defect_id FROM octane_defects")} != set(ticket_ids):
                raise ValueError("Existing scoped backup is incomplete or belongs to another ticket scope")
        finally:
            existing.close()
        return
    source = sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    temporary.unlink(missing_ok=True)
    backup = sqlite3.connect(temporary)
    try:
        source.execute("BEGIN")
        for table in (
            "octane_defects", "octane_defect_history_events",
            "octane_defect_comment_refresh_state", "octane_defect_history_refresh_state",
            "octane_ticket_evidence_refresh_state",
        ):
            definition = source.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if definition is None:
                continue
            backup.execute(definition[0])
            for start in range(0, len(ticket_ids), 900):
                batch = ticket_ids[start:start + 900]
                placeholders = ",".join("?" for _ticket_id in batch)
                cursor = source.execute(
                    f"SELECT * FROM {table} WHERE defect_id IN ({placeholders})", batch
                )
                while rows := cursor.fetchmany(1000):
                    value_placeholders = ",".join("?" for _value in rows[0])
                    backup.executemany(f"INSERT INTO {table} VALUES ({value_placeholders})", rows)
        backup.commit()
    finally:
        backup.close()
        source.close()
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the exact Shift/Tolerate evidence scope with a recoverable local backup.")
    parser.add_argument("--knowledge-base", required=True, type=Path)
    parser.add_argument("--db-path", default=get_full_picture_source_db_path(), type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--batch-size", default=50, type=int)
    parser.add_argument("--allow-unavailable", action="store_true")
    parser.add_argument("--expected-unavailable-ids", nargs="+", default=[])
    args = parser.parse_args()
    payload = json.loads(args.knowledge_base.read_text(encoding="utf-8"))
    ticket_ids = sorted({str(item["ticket_id"]) for item in payload["ticket_mappings"]})
    args.output_directory.mkdir(parents=True, exist_ok=True)
    scope_file = args.output_directory / "ticket_scope.json"
    scope = {
        "input_knowledge_base": str(args.knowledge_base.resolve()),
        "source_database": str(args.db_path.resolve()),
        "ticket_ids": ticket_ids,
    }
    if scope_file.exists() and json.loads(scope_file.read_text(encoding="utf-8")) != scope:
        raise ValueError("Existing repair directory belongs to a different source scope")
    policy_file = args.output_directory / "source_refresh_policy.json"
    policy = {"expected_unavailable_ticket_ids": sorted(set(args.expected_unavailable_ids))}
    if args.allow_unavailable != bool(policy["expected_unavailable_ticket_ids"]):
        parser.error("--allow-unavailable requires an explicit --expected-unavailable-ids list, and vice versa")
    if not set(policy["expected_unavailable_ticket_ids"]) <= set(ticket_ids):
        raise ValueError("Unavailable exception list falls outside the approved scope")
    if policy_file.exists() and json.loads(policy_file.read_text(encoding="utf-8")) != policy:
        raise ValueError("Unavailable exception policy changed; start a new reviewed repair directory")
    policy_file.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    scope_file.write_text(json.dumps(scope, indent=2), encoding="utf-8")
    backup_path = args.output_directory / "source_before_repair.sqlite3"
    print(f"Backing up exact scope: {len(ticket_ids)} tickets -> {backup_path}", flush=True)
    backup_ticket_scope(args.db_path, ticket_ids, backup_path)
    print("Scoped source backup complete", flush=True)
    summary = refresh_octane_tickets_by_ids(
        source_db_path=args.db_path,
        ticket_ids=ticket_ids,
        client=build_default_octane_client(),
        evidence_directory=args.output_directory / "fetched_evidence",
        batch_size=args.batch_size,
        allow_unavailable=args.allow_unavailable,
        expected_unavailable_ids=policy["expected_unavailable_ticket_ids"],
    )
    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    summary["source_database"] = str(args.db_path.resolve())
    summary["backup_path"] = str(backup_path.resolve())
    (args.output_directory / "source_refresh_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())