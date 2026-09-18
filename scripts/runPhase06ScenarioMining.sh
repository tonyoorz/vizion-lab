#!/usr/bin/env bash
#
# Phase 06 Field Escape Scenario Mining — end-to-end pipeline.
#
# Runs the full V2 scenario-mining pipeline on Phase 06 resolved tickets:
#   1. Export scenario extraction inputs from Phase06full.xlsx
#   2. Run LLM batch extraction (deepseek-v4-pro) on all tickets
#   3. Normalize, cluster, score, and build the final Excel workbook
#
# Usage:
#   bash scripts/runPhase06ScenarioMining.sh [options]
#
# Options:
#   --skip-llm         Skip the LLM extraction step (reuse cached outputs)
#   --concurrency N    LLM batch concurrency (default: 20)
#   --model MODEL      LLM model (default: deepseek-v4-pro)
#   --limit N          Only process N tickets (for testing)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── Defaults ──────────────────────────────────────────────────────────
SKIP_LLM=false
CONCURRENCY=20
MODEL="deepseek-v4-pro"
LIMIT_ARG=""

# ── Parse args ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-llm)      SKIP_LLM=true; shift ;;
    --concurrency)   CONCURRENCY="$2"; shift 2 ;;
    --model)         MODEL="$2"; shift 2 ;;
    --limit)         LIMIT_ARG="--limit $2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Paths ─────────────────────────────────────────────────────────────
export PYTHONIOENCODING=utf-8
PYTHON="python"

INPUTS_JSONL="exports/idcevo_phase06/llm/v2_scenario_inputs.jsonl"
OUTPUTS_JSONL="exports/idcevo_phase06/llm/v2_scenario_outputs.jsonl"
ERRORS_JSONL="${OUTPUTS_JSONL}.errors.jsonl"
FINAL_XLSX="exports/idcevo_phase06/IDCEVO_Phase06_Field_Escape_Scenario_Library_V2.xlsx"

echo "════════════════════════════════════════════════════════════════"
echo "  Phase 06 Field Escape Scenario Mining Pipeline"
echo "════════════════════════════════════════════════════════════════"
echo "  Inputs:    $INPUTS_JSONL"
echo "  Outputs:   $OUTPUTS_JSONL"
echo "  Final:     $FINAL_XLSX"
echo "  LLM:       $MODEL (concurrency=$CONCURRENCY, skip=$SKIP_LLM)"
echo ""

# ── Step 1: Export scenario extraction inputs ────────────────────────
echo "── Step 1: Export scenario extraction inputs ───────────────────"
if [[ -f "$INPUTS_JSONL" ]]; then
  INPUT_COUNT=$(wc -l < "$INPUTS_JSONL")
  echo "  $INPUTS_JSONL already exists ($INPUT_COUNT records) — reusing."
else
  $PYTHON exports/idcevo_phase06/llm/export_v2_scenario_inputs.py
fi
echo ""

# ── Step 2: LLM batch extraction ──────────────────────────────────────
if [[ "$SKIP_LLM" == "true" ]]; then
  echo "── Step 2: LLM extraction SKIPPED (--skip-llm) ────────────────"
else
  echo "── Step 2: LLM batch extraction ────────────────────────────────"
  echo "  This may take ~1.5 hours for full 19k tickets at concurrency $CONCURRENCY."
  node scripts/extractPhase09Scenarios.mjs \
    --input "$INPUTS_JSONL" \
    --output "$OUTPUTS_JSONL" \
    --error-output "$ERRORS_JSONL" \
    --model "$MODEL" \
    --concurrency "$CONCURRENCY" \
    --max-retries 3 \
    $LIMIT_ARG || true
fi

# Deduplicate outputs
echo ""
echo "  Deduplicating outputs..."
$PYTHON scripts/dedup_jsonl.py "$OUTPUTS_JSONL" || true
echo ""

# ── Step 3: Build the final Excel workbook ────────────────────────────
echo "── Step 3: Build Phase 06 Field Escape Scenario Library Excel ───"
$PYTHON backend/analytics/phase06_idcevo_scenario_build.py
echo ""

echo "════════════════════════════════════════════════════════════════"
echo "  Pipeline complete."
echo "  Final Excel: $FINAL_XLSX"
echo "════════════════════════════════════════════════════════════════"
