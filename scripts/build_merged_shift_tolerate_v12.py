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
    assess_post_target_recurrence,
    build_merged_shift_tolerate_artifact_triple,
    load_open_shift_raw_evidence,
    load_shift_full_records_from_source_workbook,
    load_ticket_creation_times,
)
from backend.analytics.open_ticket_postponement import load_validated_llm_summaries
from backend.analytics.phase09_process_v12 import (
    build_process_full_records,
    merge_shift_tolerate_records,
)
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
    REPOSITORY_ROOT
    / "exports"
    / "idcevo_phase09"
    / "V4"
    / "merged_shift_tolerate_v12"
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
        sheet = workbook["06_全部明细"]
        rows = sheet.iter_rows(values_only=True)
        headers = [_text(value) for value in next(rows)]
        header_index = {header: index for index, header in enumerate(headers)}

        def cell(row: tuple[object, ...], header: str) -> str:
            index = header_index.get(header)
            return _text(row[index]) if index is not None and index < len(row) else ""

        summaries: dict[str, dict[str, object]] = {}
        for row in rows:
            ticket_id = cell(row, "Ticket ID")
            if not ticket_id:
                continue
            summaries[ticket_id] = {
                "issue_summary": cell(row, "LLM 问题摘要"),
                "postponement_reason_analysis": cell(row, "LLM 延期原因分析"),
                "resolution_timing_analysis": cell(row, "LLM 解决时间分析"),
                "resolution_version_analysis": cell(row, "LLM 解决版本分析"),
                "postponement_assessment": cell(row, "LLM 延期/计划判断"),
                "review_focus": cell(row, "LLM 待核实"),
                "llm_model": cell(row, "LLM Model"),
            }
        return summaries
    finally:
        workbook.close()


def _phase09_issue_summary(value: object) -> str:
    text = _text(value)
    if not text:
        return "No existing Phase 09 LLM issue summary retained."
    if "问题：" in text:
        text = text.split("问题：", 1)[1].split("\n", 1)[0]
    return text[:500]


def _read_phase09_summaries(path: Path) -> dict[str, dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[6]
        rows = sheet.iter_rows(values_only=True)
        headers = [_text(value) for value in next(rows)]
        header_index = {header: index for index, header in enumerate(headers)}

        def cell(
            row: tuple[object, ...],
            header: str,
            fallback: int | None = None,
        ) -> str:
            index = header_index.get(header, fallback)
            return _text(row[index]) if index is not None and index < len(row) else ""

        summaries: dict[str, dict[str, object]] = {}
        for row in rows:
            ticket_id = cell(row, "Ticket ID", 0)
            if not ticket_id:
                continue
            llm_summary = cell(row, "LLM 处理摘要", 14)
            summaries[ticket_id] = {
                "issue_summary": _phase09_issue_summary(llm_summary),
                "postponement_reason_analysis": (
                    "Process evidence is recorded; see the Future Resolution Plan "
                    "leaf and matched process evidence."
                ),
                "resolution_timing_analysis": (
                    "Use recorded process evidence to verify the actual decision date, "
                    "target week, and release milestone."
                ),
                "resolution_version_analysis": (
                    "Use recorded target/release fields in the Future Resolution Plan "
                    "leaf; do not treat process evidence as delivery proof."
                ),
                "postponement_assessment": llm_summary,
                "review_focus": (
                    "Verify ticket-specific validation and delivery evidence before "
                    "communicating a resolution."
                ),
                "llm_model": "existing_phase09_fullllm + process_evidence_rule",
                "rca_root_cause": cell(row, "RCA Root Cause"),
                "rca_recovery": cell(row, "RCA Recovery Action"),
                "rca_observed": cell(row, "RCA Observed Behaviour"),
                "rca_expected": cell(row, "RCA Expected Behaviour"),
                "rca_evidence_source": "Phase 09 FullLLM 06_全部明细",
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
        ticket_id: {**summary, "llm_model": _text(summary.get("model"))}
        for ticket_id, summary in summaries.items()
    }


def _load_recurrence_corpus(db_path: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT defect_id AS ticket_id,
                   name AS title,
                   phase,
                   software_version,
                   solution_cluster,
                   product_areas,
                   defect_category
            FROM octane_defects
            WHERE trim(coalesce(software_version, '')) <> ''
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a merged Open Shift and strict Shift/Tolerate process V12 "
            "candidate with ticket-level future-resolution branches."
        )
    )
    parser.add_argument("--process-inputs", default=None)
    parser.add_argument("--process-summaries", default=None)
    parser.add_argument("--open-full-llm", default=str(OPEN_FULLLLM))
    parser.add_argument("--output-directory", default=str(OUTPUT_DIRECTORY))
    return parser


def main() -> int:
    args = _parser().parse_args()
    db_path = REPOSITORY_ROOT / "database" / "source" / "qgate_raw.db"
    process_records = load_shift_tolerate_process_records(db_path, BASELINE_FULLLLM)
    summaries = {
        **_read_phase09_summaries(BASELINE_FULLLLM),
        **_read_open_v3_summaries(Path(args.open_full_llm)),
        **_read_process_v3_summaries(
            Path(args.process_inputs) if args.process_inputs else None,
            Path(args.process_summaries) if args.process_summaries else None,
        ),
    }
    strict_process_records = build_process_full_records(process_records, summaries)
    open_shift_records = load_shift_full_records_from_source_workbook(
        args.open_full_llm
    )
    merged_records = merge_shift_tolerate_records(
        strict_process_records,
        open_shift_records,
    )
    creation_times = load_ticket_creation_times(
        db_path,
        [_text(record.get("ticket_id")) for record in merged_records],
    )
    for record in merged_records:
        record["creation_time"] = creation_times.get(_text(record.get("ticket_id")), "")
    merged_records = assess_post_target_recurrence(
        merged_records,
        _load_recurrence_corpus(db_path),
    )
    raw_evidence = load_open_shift_raw_evidence(
        db_path,
        [_text(record.get("ticket_id")) for record in merged_records],
    )
    outputs = build_merged_shift_tolerate_artifact_triple(
        merged_records,
        output_directory=Path(args.output_directory),
        source_snapshot=_source_snapshot(db_path),
        raw_evidence=raw_evidence,
    )
    open_count = sum(
        _text(record.get("ticket_lifecycle_status")) == "Open / active"
        for record in merged_records
    )
    print(f"Strict process records: {len(strict_process_records)}")
    print(f"Open Shift records: {len(open_shift_records)}")
    print(f"Merged unique ticket records: {len(merged_records)}")
    print(f"Merged open records: {open_count}")
    print(f"Merged Phase 09 records: {len(merged_records) - open_count}")
    print(f"Merged raw evidence rows: {len(raw_evidence)}")
    for status in (
        "recurrence_observed",
        "no_recurrence_evidence",
        "comparison_unavailable",
    ):
        print(
            f"Resolution verification {status}: "
            f"{sum(_text(record.get('resolution_verification_status')) == status for record in merged_records)}"
        )
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())