#!/usr/bin/env bash
#
# V2 Field Escape Scenario Mining — end-to-end pipeline driver.
#
# Runs the full pipeline:
#   1. Export scenario extraction inputs from the two source Excel files
#   2. Run LLM batch extraction (deepseek-v4-pro) on all tickets
#   3. Normalize, cluster, score, and build the final Excel workbook
#
# Usage:
#   bash scripts/runFieldEscapeScenarioMining.sh [--skip-llm] [--concurrency N]
#
# Options:
#   --skip-llm        Skip the LLM extraction step (reuse cached outputs)
#   --concurrency N   LLM batch concurrency (default: 20)
#   --model MODEL     LLM model (default: deepseek-v4-pro)
#   --limit N         Only process N tickets (for testing)
#
# Prerequisites:
#   - Python 3.13+ with openpyxl
#   - Node.js with the vizion-lab server dependencies (loadLocalEnv)
#   - .env file with DUPSEARCH_BEACON_API_KEY (or equivalent LLM credentials)
#   - Source Excel files in exports/idcevo_phase09/:
#     - IDCEVO_Phase09_Closure_Risk_20260908_FullLLM_Complete.xlsx
#     - Full 09 analysis.xlsx
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
INPUTS_JSONL="exports/idcevo_phase09/llm/v2_scenario_18000_inputs.jsonl"
OUTPUTS_JSONL="exports/idcevo_phase09/llm/v2_scenario_18000_outputs.jsonl"
ERRORS_JSONL="${OUTPUTS_JSONL}.errors.jsonl"
FINAL_XLSX="exports/idcevo_phase09/IDCEVO_Phase09_Field_Escape_Scenario_Library_V2.xlsx"
PYTHON="python"
# Force UTF-8 output on Windows
export PYTHONIOENCODING=utf-8

echo "════════════════════════════════════════════════════════════════"
echo "  V2 Field Escape Scenario Mining Pipeline"
echo "════════════════════════════════════════════════════════════════"
echo "  Repo:      $REPO_ROOT"
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
  echo "  Delete it to force re-export."
else
  $PYTHON exports/idcevo_phase09/llm/export_v2_scenario_inputs.py
fi
echo ""

# ── Step 2: LLM batch extraction ──────────────────────────────────────
if [[ "$SKIP_LLM" == "true" ]]; then
  echo "── Step 2: LLM extraction SKIPPED (--skip-llm) ────────────────"
  echo "  Reusing existing outputs in $OUTPUTS_JSONL"
else
  echo "── Step 2: LLM batch extraction ────────────────────────────────"
  echo "  This may take several hours for full 18k tickets."
  echo "  The batch runner supports resume — it skips already-processed tickets."
  echo ""
  node scripts/extractPhase09Scenarios.mjs \
    --input "$INPUTS_JSONL" \
    --output "$OUTPUTS_JSONL" \
    --error-output "$ERRORS_JSONL" \
    --model "$MODEL" \
    --concurrency "$CONCURRENCY" \
    --max-retries 3 \
    $LIMIT_ARG
fi

# Deduplicate outputs (in case of partial-run overlaps)
echo ""
echo "  Deduplicating outputs..."
$PYTHON scripts/dedup_jsonl.py "$OUTPUTS_JSONL"
echo ""

# ── Step 3: Build the final Excel workbook ────────────────────────────
echo "── Step 3: Build Field Escape Scenario Library Excel ─────────────"
$PYTHON backend/analytics/phase09_idcevo_scenario_build.py
echo ""

# ── Done ─────────────────────────────────────────────────────────────
echo "════════════════════════════════════════════════════════════════"
echo "  Pipeline complete."
echo "  Final Excel: $FINAL_XLSX"
echo "════════════════════════════════════════════════════════════════"
