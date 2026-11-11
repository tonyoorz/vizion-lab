from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.open_ticket_postponement import (
    load_open_ticket_records,
    load_validated_llm_summaries,
    select_open_shift_ticket_records,
    write_llm_input_jsonl,
    write_open_shift_ticket_report,
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
        description="Export open shift-ticket plan evidence, LLM inputs, and an Excel workbook."
    )
    parser.add_argument(
        "--db-path",
        default="database/source/qgate_raw.db",
        help="Source Octane SQLite database path.",
    )
    parser.add_argument(
        "--inputs-output",
        required=True,
        help="Output JSONL path for bounded v2 LLM inputs.",
    )
    parser.add_argument(
        "--llm-summaries",
        default=None,
        help="Optional v2 LLM JSONL to merge after fingerprint validation.",
    )
    parser.add_argument("--output", required=True, help="Output Excel workbook path.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    candidates = select_open_shift_ticket_records(
        load_open_ticket_records(args.db_path)
    )
    input_records = write_llm_input_jsonl(candidates, args.inputs_output)
    summaries = load_validated_llm_summaries(input_records, args.llm_summaries)
    workbook_path = write_open_shift_ticket_report(
        candidates,
        output_path=args.output,
        llm_summaries=summaries,
        source_snapshot=_source_snapshot(args.db_path),
    )
    print(f"Open shift-plan candidates: {len(candidates)}")
    print(f"Validated LLM summaries: {len(summaries)}")
    print(f"LLM inputs: {args.inputs_output}")
    print(f"Workbook: {workbook_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())