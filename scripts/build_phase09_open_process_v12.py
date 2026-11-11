from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from openpyxl import load_workbook


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.open_shift_scenario_triple import (
    build_open_shift_artifact_triple,
    load_open_shift_raw_evidence,
)
from backend.analytics.open_ticket_postponement import load_validated_llm_summaries
from backend.analytics.phase09_process_v12 import build_process_full_records
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
OUTPUT_DIRECTORY = (
    REPOSITORY_ROOT / "exports" / "idcevo_phase09" / "V4" / "process_v12"
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _source_snapshot(db_path: Path) -> str:
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("SELECT max(fetched_at) FROM octane_defects").fetchone()
    finally:
        connection.close()
    return str(row[0] or "") if row else ""


def _read_open_v3_summaries(path: Path) -> dict[str, dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[1]
        summaries: dict[str, dict[str, object]] = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            ticket_id = _text(row[0])
            if not ticket_id:
                continue
            summaries[ticket_id] = {
                "issue_summary": _text(row[18]),
                "postponement_reason_analysis": _text(row[19]),
                "resolution_timing_analysis": _text(row[20]),
                "resolution_version_analysis": _text(row[21]),
                "postponement_assessment": _text(row[22]),
                "review_focus": _text(row[23]),
                "llm_model": _text(row[30]),
            }
        return summaries
    finally:
        workbook.close()


def _phase09_issue_summary(value: object) -> str:
    text = _text(value)
    if not text:
        return "No existing Phase 09 LLM issue summary retained."
    marker = "问题："
    if marker in text:
        text = text.split(marker, 1)[1]
        text = text.split("\n", 1)[0]
    return text[:500]


def _read_phase09_summaries(path: Path) -> dict[str, dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[6]
        summaries: dict[str, dict[str, object]] = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            ticket_id = _text(row[0])
            if not ticket_id:
                continue
            issue_summary = _phase09_issue_summary(row[14])
            summaries[ticket_id] = {
                "issue_summary": issue_summary,
                "postponement_reason_analysis": "Process evidence is recorded; see the Future Resolution Plan leaf and matched process evidence.",
                "resolution_timing_analysis": "Use recorded process evidence to verify the actual decision date, target week, and release milestone.",
                "resolution_version_analysis": "Use recorded target/release fields in the Future Resolution Plan leaf; do not treat process evidence as delivery proof.",
                "postponement_assessment": _text(row[14]),
                "review_focus": "Verify ticket-specific validation and delivery evidence before communicating a resolution.",
                "llm_model": "existing_phase09_fullllm + process_evidence_rule",
            }
        return summaries
    finally:
        workbook.close()


def _read_process_v3_summaries(
    inputs_path: Path | None,
    summaries_path: Path | None,
) -> dict[str, dict[str, object]]:
    if inputs_path is None or summaries_path is None:
        return {}
    if not inputs_path.exists() or not summaries_path.exists():
        return {}
    inputs = [
        json.loads(line)
        for line in inputs_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summaries = load_validated_llm_summaries(inputs, summaries_path)
    return {
        ticket_id: {
            **summary,
            "llm_model": _text(summary.get("model")),
        }
        for ticket_id, summary in summaries.items()
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a native V12 decision-tree triple for strict Shift/Tolerate process tickets."
    )
    parser.add_argument("--process-inputs", default=None)
    parser.add_argument("--process-summaries", default=None)
    parser.add_argument("--output-directory", default=str(OUTPUT_DIRECTORY))
    return parser


def main() -> int:
    args = _parser().parse_args()
    db_path = REPOSITORY_ROOT / "database" / "source" / "qgate_raw.db"
    process_records = load_shift_tolerate_process_records(db_path, BASELINE_FULLLLM)
    open_summaries = _read_open_v3_summaries(OPEN_FULLLLM)
    phase09_summaries = _read_phase09_summaries(BASELINE_FULLLLM)
    process_summaries = _read_process_v3_summaries(
        Path(args.process_inputs) if args.process_inputs else None,
        Path(args.process_summaries) if args.process_summaries else None,
    )
    summaries = {**phase09_summaries, **open_summaries, **process_summaries}
    full_records = build_process_full_records(process_records, summaries)
    raw_evidence = load_open_shift_raw_evidence(
        db_path,
        [_text(record.get("ticket_id")) for record in full_records],
    )
    outputs = build_open_shift_artifact_triple(
        full_records,
        output_directory=Path(args.output_directory),
        source_snapshot=_source_snapshot(db_path),
        raw_evidence=raw_evidence,
    )
    print(f"Strict process records: {len(process_records)}")
    print(f"Process V12 full records: {len(full_records)}")
    print(f"Process v3 summaries merged: {len(process_summaries)}")
    print(f"Process raw evidence rows: {len(raw_evidence)}")
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())