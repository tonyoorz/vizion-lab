from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.analytics.phase09_wide_postponement_scan import (
    load_shift_tolerate_process_records,
    write_shift_tolerate_process_excel,
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
        description="Generate a strict Shift/Tolerate Process Only evidence workbook for Phase 09 and current open tickets."
    )
    parser.add_argument("--db-path", default="database/source/qgate_raw.db")
    parser.add_argument("--baseline-full-llm", default=DEFAULT_BASELINE)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    records = load_shift_tolerate_process_records(
        args.db_path,
        args.baseline_full_llm,
    )
    output = write_shift_tolerate_process_excel(
        records,
        output_path=args.output,
        source_snapshot=_source_snapshot(args.db_path),
    )
    counts: dict[str, int] = {}
    for record in records:
        tier = str(record.get("process_tier") or "")
        counts[tier] = counts.get(tier, 0) + 1
    print(f"Strict Shift/Tolerate process candidates: {len(records)}")
    for tier, count in sorted(counts.items()):
        print(f"{tier}: {count}")
    print(f"Excel: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())