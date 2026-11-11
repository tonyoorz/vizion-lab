from __future__ import annotations

import argparse
from pathlib import Path
import sys

from openpyxl import load_workbook


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.open_shift_scenario_triple import load_open_shift_raw_evidence
from backend.analytics.open_ticket_postponement import write_llm_input_jsonl
from backend.analytics.phase09_postponed_v4 import _evidence_text_by_ticket
from backend.analytics.phase09_wide_postponement_scan import (
    load_shift_tolerate_process_records,
)


BASELINE_FULLLLM = (
    REPOSITORY_ROOT
    / "exports"
    / "idcevo_phase09"
    / "IDCEVO_Phase09_Closure_Risk_20260908_FinalProject_LLM_Complete.xlsx"
)
OPEN_FULLLLM = (
    REPOSITORY_ROOT
    / "exports"
    / "open_shift_tickets"
    / "v3_candidate"
    / "native_v12"
    / "IDCEVO_Open_Shift_Tickets_20261015_FullLLM.xlsx"
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _existing_open_summary_ids(path: Path) -> set[str]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        return {
            _text(row[0])
            for row in workbook.worksheets[1].iter_rows(min_row=2, values_only=True)
            if _text(row[0])
        }
    finally:
        workbook.close()


def _process_plan(record: dict[str, object]) -> str:
    lines = [
        "Process tier: {}".format(_text(record.get("process_tier"))),
        "Process basis: {}".format(_text(record.get("process_basis"))),
    ]
    matches = record.get("process_matches")
    if isinstance(matches, list):
        for match in matches:
            if isinstance(match, dict):
                lines.append(
                    "{} {}: {}".format(
                        _text(match.get("source")),
                        _text(match.get("field_name")),
                        _text(match.get("excerpt")),
                    ).strip()
                )
    return "\n".join(line for line in lines if line.strip())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export missing v3 LLM inputs for current-open strict Shift/Tolerate process tickets."
    )
    parser.add_argument("--db-path", default="database/source/qgate_raw.db")
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    process_records = load_shift_tolerate_process_records(args.db_path, BASELINE_FULLLLM)
    existing_ids = _existing_open_summary_ids(OPEN_FULLLLM)
    missing_records = [
        record
        for record in process_records
        if _text(record.get("cohort")) == "Current open"
        and _text(record.get("ticket_id")) not in existing_ids
    ]
    raw_evidence = load_open_shift_raw_evidence(
        args.db_path,
        [_text(record.get("ticket_id")) for record in missing_records],
    )
    evidence_text = _evidence_text_by_ticket(raw_evidence)
    input_records = []
    for record in missing_records:
        ticket_id = _text(record.get("ticket_id"))
        evidence = evidence_text.get(ticket_id, {})
        input_records.append(
            {
                "ticket_id": ticket_id,
                "name": _text(record.get("title")),
                "phase": _text(record.get("current_phase")),
                "software_version": _text(record.get("source_software_version")),
                "description": _text(evidence.get("description")),
                "comments": _text(evidence.get("comments")),
                "history_evidence": _text(evidence.get("history_evidence")),
                "confidence_category": _text(record.get("process_tier")),
                "confidence_basis": _text(record.get("process_basis")),
                "plan_evidence_signals": _process_plan(record),
                "postponement_document": _process_plan(record),
            }
        )
    inputs = write_llm_input_jsonl(input_records, args.output)
    print(f"Strict process current-open tickets: {sum(_text(record.get('cohort')) == 'Current open' for record in process_records)}")
    print(f"Existing v3 open summaries: {len(existing_ids)}")
    print(f"Missing process LLM inputs: {len(inputs)}")
    print(f"Raw evidence rows for missing inputs: {len(raw_evidence)}")
    print(f"Inputs: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())