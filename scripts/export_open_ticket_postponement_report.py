from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sqlite3
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.open_ticket_postponement import (
    SHEET_CLASSIFICATIONS,
    load_open_ticket_records,
    load_validated_llm_summaries,
    write_confirmed_postponed_csv,
    write_llm_input_jsonl,
    write_open_ticket_postponement_report,
)


def _source_snapshot(db_path: str | Path) -> str:
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("SELECT max(fetched_at) FROM octane_defects").fetchone()
    finally:
        connection.close()
    return str(row[0] or "") if row else ""


def _reportable_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    reportable_classifications = {classification for _, classification in SHEET_CLASSIFICATIONS}
    return [
        record
        for record in records
        if str(record.get("classification") or "") in reportable_classifications
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export open-ticket postponement evidence, LLM inputs, and Excel/CSV report."
    )
    parser.add_argument(
        "--db-path",
        default="database/source/qgate_raw.db",
        help="Source Octane SQLite database path.",
    )
    parser.add_argument(
        "--inputs-output",
        required=True,
        help="Output JSONL path for bounded LLM inputs.",
    )
    parser.add_argument(
        "--llm-summaries",
        default=None,
        help="Optional LLM JSONL to merge after fingerprint validation.",
    )
    parser.add_argument("--output", required=True, help="Output Excel workbook path.")
    parser.add_argument("--csv-output", required=True, help="Output confirmed-postponed CSV path.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    records = _reportable_records(load_open_ticket_records(args.db_path))
    input_records = write_llm_input_jsonl(records, args.inputs_output)
    summaries = load_validated_llm_summaries(input_records, args.llm_summaries)
    workbook_path = write_open_ticket_postponement_report(
        records,
        output_path=args.output,
        llm_summaries=summaries,
        source_snapshot=_source_snapshot(args.db_path),
    )
    csv_path = write_confirmed_postponed_csv(
        records,
        output_path=args.csv_output,
        llm_summaries=summaries,
    )
    counts = Counter(str(record.get("classification") or "") for record in records)
    print(f"Open-ticket postponement records: {len(records)}")
    for _sheet_name, classification in SHEET_CLASSIFICATIONS:
        print(f"{classification}: {counts[classification]}")
    print(f"Validated LLM summaries: {len(summaries)}")
    print(f"LLM inputs: {args.inputs_output}")
    print(f"Workbook: {workbook_path}")
    print(f"Confirmed CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())