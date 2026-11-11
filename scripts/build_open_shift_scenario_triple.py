from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.open_shift_scenario_triple import (
    build_open_shift_artifact_triple,
    build_shift_scenarios,
    load_open_shift_full_records,
    load_open_shift_raw_evidence,
)


def _source_snapshot(db_path: str | Path) -> str:
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("SELECT max(fetched_at) FROM octane_defects").fetchone()
    finally:
        connection.close()
    return str(row[0] or "") if row else ""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the open-shift FullLLM source workbook and V12-style future-resolution evidence triple."
    )
    parser.add_argument(
        "--db-path",
        default="database/source/qgate_raw.db",
        help="Source Octane SQLite database path.",
    )
    parser.add_argument("--inputs", required=True, help="Current v2 open-shift LLM inputs JSONL.")
    parser.add_argument("--summaries", required=True, help="Validated v2 open-shift LLM summaries JSONL.")
    parser.add_argument("--output-directory", required=True, help="Candidate artifact output directory.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    source_snapshot = _source_snapshot(args.db_path)
    full_records = load_open_shift_full_records(
        args.db_path,
        args.inputs,
        args.summaries,
    )
    raw_evidence = load_open_shift_raw_evidence(
        args.db_path,
        [str(record.get("ticket_id") or "") for record in full_records],
    )
    outputs = build_open_shift_artifact_triple(
        full_records,
        output_directory=args.output_directory,
        source_snapshot=source_snapshot,
        raw_evidence=raw_evidence,
    )
    scenarios = build_shift_scenarios(full_records)
    print(f"Open shift FullLLM ticket records: {len(full_records)}")
    print(f"Open shift raw evidence rows: {len(raw_evidence)}")
    print(f"Open shift scenarios: {len(scenarios)}")
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())