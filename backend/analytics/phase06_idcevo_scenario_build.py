#!/usr/bin/env python3
"""
Phase 06 Field Escape Scenario Library Builder.

Reuses the Phase 09 scenario-build pipeline by overriding the path constants
to point at Phase 06 source data and LLM extraction outputs. Produces the
same multi-sheet Excel structure as the Phase 09 Scenario Library.

Phase 06 source: exports/idcevo_phase06/Phase06full.xlsx
Phase 06 LLM outputs: exports/idcevo_phase06/llm/v2_scenario_outputs.jsonl
Output: exports/idcevo_phase06/IDCEVO_Phase06_Field_Escape_Scenario_Library_V2.xlsx
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on the path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Import the Phase 09 build module and override its path constants
import openpyxl  # noqa: E402
from collections import Counter  # noqa: E402
import backend.analytics.phase09_idcevo_scenario_build as build_mod  # noqa: E402

# Override paths for Phase 06
PHASE06_LLM_DIR = REPO_ROOT / "exports" / "idcevo_phase06" / "llm"
PHASE06_EXCEL_PATH = REPO_ROOT / "exports" / "idcevo_phase06" / "Phase06full.xlsx"
PHASE06_EXTRACTION_JSONL = PHASE06_LLM_DIR / "v2_scenario_outputs.jsonl"
PHASE06_OUTPUT_XLSX = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase06"
    / "IDCEVO_Phase06_Field_Escape_Scenario_Library_V2.xlsx"
)


def main() -> int:
    # Override module-level path constants used by load_extractions / load_excel_metadata
    build_mod.EXCEL_PATH = PHASE06_EXCEL_PATH
    build_mod.EXTRACTION_JSONL = PHASE06_EXTRACTION_JSONL
    build_mod.OUTPUT_XLSX = PHASE06_OUTPUT_XLSX
    build_mod.LLM_DIR = PHASE06_LLM_DIR

    extractions = build_mod.load_extractions()
    metadata = build_mod.load_excel_metadata()

    scenarios, excluded = build_mod.build_scenarios(extractions, metadata)
    families = build_mod.build_families(scenarios)
    risk_records = build_mod.build_risk_scores(scenarios, len(extractions))
    knowledge_cards = build_mod.build_knowledge_cards(scenarios, families)
    # Call build_excel with explicit output path (the default param is bound at
    # definition time to the Phase 09 path, so we must pass it explicitly)
    build_mod.build_excel(
        scenarios, families, risk_records, knowledge_cards,
        excluded, extractions, metadata,
        output_xlsx=PHASE06_OUTPUT_XLSX,
    )

    # Summary
    print(f"\n{'=' * 60}")
    print("PHASE 06 SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total tickets: {len(extractions)}")
    print(f"  Field scenario tickets: {sum(s['ticket_count'] for s in scenarios)}")
    print(f"  Excluded tickets: {len(excluded)}")
    print(f"  Total scenarios: {len(scenarios)}")
    print(f"  Total families: {len(families)}")
    band_dist = Counter(s["risk_band"] for s in scenarios)
    print(f"\n  Risk band distribution:")
    for band in ["Critical", "High", "Medium", "Low"]:
        print(f"    {band}: {band_dist.get(band, 0)}")
    print(f"\n  Top 10 families:")
    for f in families[:10]:
        print(f"    {f['family_id']}  {f['family_name']}: {f['scenario_count']} scenarios, {f['ticket_count']} tickets")
    print(f"\n  Sheets: {openpyxl.load_workbook(PHASE06_OUTPUT_XLSX, read_only=True).sheetnames}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
