from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.phase09_postponed_v4 import (
    enrich_phase09_postponed_v4_records,
    load_phase09_postponed_v4_records,
    load_phase09_postponed_v4_summaries,
    write_phase09_postponed_v4_excel,
    write_phase09_postponed_v4_inputs,
)


DEFAULT_BASELINE = (
    "exports/idcevo_phase09/IDCEVO_Phase09_Closure_Risk_20260908_FinalProject_LLM_Complete.xlsx"
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
        description="Build Phase 09 postponed / validation-gap V4 inputs and FullLLM-style Excel."
    )
    parser.add_argument("--db-path", default="database/source/qgate_raw.db")
    parser.add_argument("--baseline-full-llm", default=DEFAULT_BASELINE)
    parser.add_argument("--inputs-output", required=True)
    parser.add_argument("--summaries", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--model-label", default="glm-5.3-flash")
    return parser


def main() -> int:
    args = _parser().parse_args()
    records, raw_evidence = load_phase09_postponed_v4_records(
        args.db_path,
        args.baseline_full_llm,
    )
    inputs = write_phase09_postponed_v4_inputs(records, args.inputs_output)
    print(f"Phase 09 V4 postponed candidates: {len(records)}")
    print(f"Phase 09 V4 raw evidence rows: {len(raw_evidence)}")
    print(f"Phase 09 V4 inputs: {args.inputs_output}")
    if not args.summaries:
        return 0
    if not args.output:
        raise SystemExit("--output is required when --summaries is provided")
    summaries = load_phase09_postponed_v4_summaries(inputs, args.summaries)
    enriched = enrich_phase09_postponed_v4_records(records, summaries)
    output = write_phase09_postponed_v4_excel(
        enriched,
        raw_evidence=raw_evidence,
        output_path=args.output,
        source_snapshot=_source_snapshot(args.db_path),
        model_label=args.model_label,
    )
    print(f"Phase 09 V4 validated GLM summaries: {len(summaries)}")
    print(f"Phase 09 V4 rendered tickets: {len(enriched)}")
    print(f"Phase 09 V4 Excel: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())