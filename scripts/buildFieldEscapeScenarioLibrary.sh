#!/usr/bin/env bash
#
# Field Escape Scenario Library — end-to-end builder from Octane DB to final Excel.
#
# This script runs the ENTIRE pipeline:
#
#   Phase A (upstream): Octane SQLite DB → Source Excel A + Source Excel B
#     1. Build report payload from qgate_raw.db + export v7 LLM summary inputs
#     2. Run v7 LLM summary batch (deepseek-v4-pro)
#     3. Generate source Excel A (with evidence sheet that becomes Excel B)
#     4. Backfill v7 LLM summaries into source Excel A
#     5. Extract evidence sheet to source Excel B
#
#   Phase B (V2 pipeline): Source Excel A + B → Field Escape Scenario Library
#     6. Export V2 scenario extraction inputs
#     7. Run V2 LLM scenario extraction batch
#     8. Normalize, cluster, score, build final Excel
#
# Usage:
#   bash scripts/buildFieldEscapeScenarioLibrary.sh [options]
#
# Options:
#   --db PATH            Path to qgate_raw.db (default: database/source/qgate_raw.db)
#   --model MODEL        LLM model for both v7 and V2 passes (default: deepseek-v4-pro)
#   --v7-concurrency N   v7 LLM summary concurrency (default: 5)
#   --v2-concurrency N   V2 scenario extraction concurrency (default: 20)
#   --skip-upstream      Skip Phase A (reuse existing source Excels)
#   --skip-v2-llm        Skip Phase B Step 7 (reuse cached V2 LLM outputs)
#   --skip-v7-llm        Skip Phase A Step 2 (reuse cached v7 summaries)
#   --limit N            Only process N tickets in LLM batches (for testing)
#   --help               Show this help
#
# Prerequisites:
#   - Python 3.13+ with openpyxl, installed in the repo's .venv or system Python
#   - Node.js with vizion-lab server dependencies
#   - .env file with DUPSEARCH_BEACON_API_KEY (or equivalent LLM credentials)
#   - database/source/qgate_raw.db (20 GB SQLite staged from BMW Octane)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── Defaults ──────────────────────────────────────────────────────────
DB_PATH="database/source/qgate_raw.db"
MODEL="deepseek-v4-pro"
V7_CONCURRENCY=5
V2_CONCURRENCY=20
SKIP_UPSTREAM=false
SKIP_V2_LLM=false
SKIP_V7_LLM=false
LIMIT_ARG=""

# ── Parse args ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)              DB_PATH="$2"; shift 2 ;;
    --model)           MODEL="$2"; shift 2 ;;
    --v7-concurrency)  V7_CONCURRENCY="$2"; shift 2 ;;
    --v2-concurrency)  V2_CONCURRENCY="$2"; shift 2 ;;
    --skip-upstream)   SKIP_UPSTREAM=true; shift ;;
    --skip-v2-llm)     SKIP_V2_LLM=true; shift ;;
    --skip-v7-llm)     SKIP_V7_LLM=true; shift ;;
    --limit)           LIMIT_ARG="--limit $2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Paths ─────────────────────────────────────────────────────────────
export PYTHONIOENCODING=utf-8
PYTHON="python"
NODE="node"

EXPORT_DIR="exports/idcevo_phase09"
LLM_DIR="$EXPORT_DIR/llm"

# Upstream (Phase A) paths
V7_INPUTS="$LLM_DIR/phase09_all_pending_13730_inputs_v7.jsonl"
V7_OUTPUTS="$LLM_DIR/phase09_all_pending_summaries_v7_pro.jsonl"
V7_ERRORS="${V7_OUTPUTS}.errors.jsonl"
SOURCE_EXCEL_A="$EXPORT_DIR/IDCEVO_Phase09_Closure_Risk_$(date +%Y%m%d)_FullLLM_Complete.xlsx"
SOURCE_EXCEL_A_PRELLM="${SOURCE_EXCEL_A/_FullLLM_Complete/_PreLLM}"
SOURCE_EXCEL_B="$EXPORT_DIR/Full 09 analysis.xlsx"

# V2 pipeline (Phase B) paths
V2_INPUTS="$LLM_DIR/v2_scenario_18000_inputs.jsonl"
V2_OUTPUTS="$LLM_DIR/v2_scenario_18000_outputs.jsonl"
V2_ERRORS="${V2_OUTPUTS}.errors.jsonl"
FINAL_XLSX="$EXPORT_DIR/IDCEVO_Phase09_Field_Escape_Scenario_Library_V2.xlsx"

echo "════════════════════════════════════════════════════════════════════"
echo "  Field Escape Scenario Library — End-to-End Builder"
echo "════════════════════════════════════════════════════════════════════"
echo "  Repo:            $REPO_ROOT"
echo "  DB:              $DB_PATH"
echo "  LLM model:       $MODEL"
echo "  Source Excel A:  $SOURCE_EXCEL_A"
echo "  Source Excel B:  $SOURCE_EXCEL_B"
echo "  Final Excel:     $FINAL_XLSX"
echo "  Skip upstream:   $SKIP_UPSTREAM"
echo "  Skip v7 LLM:     $SKIP_V7_LLM"
echo "  Skip V2 LLM:     $SKIP_V2_LLM"
echo ""

# ═══════════════════════════════════════════════════════════════════════
# PHASE A: Upstream — DB → Source Excels
# ═══════════════════════════════════════════════════════════════════════

if [[ "$SKIP_UPSTREAM" == "true" ]]; then
  echo "── Phase A SKIPPED (--skip-upstream) ────────────────────────────"
  echo "  Reusing existing source Excels."
  echo ""
else
  # ── Step A1: Build report payload from DB + export v7 LLM summary inputs ──
  echo "── Step A1: Build report payload from DB + export v7 inputs ─────"
  if [[ -f "$V7_INPUTS" ]]; then
    V7_INPUT_COUNT=$(wc -l < "$V7_INPUTS")
    echo "  $V7_INPUTS already exists ($V7_INPUT_COUNT records) — reusing."
  else
    echo "  Building from DB: $DB_PATH"
    $PYTHON -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from backend.analytics.phase09_idcevo_llm_summary import export_phase09_llm_summary_inputs
count = export_phase09_llm_summary_inputs(
    db_path='$DB_PATH',
    output_path='$V7_INPUTS',
    include_china_specific=True,
    review_only=False,
)
print(f'  Wrote {count} v7 LLM summary input records')
"
  fi
  echo ""

  # ── Step A2: Run v7 LLM summary batch ────────────────────────────────
  echo "── Step A2: v7 LLM summary batch ────────────────────────────────"
  if [[ "$SKIP_V7_LLM" == "true" ]]; then
    echo "  SKIPPED (--skip-v7-llm) — reusing existing v7 summaries."
  else
    if [[ -f "$V7_OUTPUTS" ]]; then
      V7_DONE=$(wc -l < "$V7_OUTPUTS")
      echo "  Found $V7_DONE existing summaries — batch runner will resume."
    fi
    echo "  Running v7 LLM summary batch (model=$MODEL, concurrency=$V7_CONCURRENCY)..."
    $NODE scripts/summarizePhase09LlmInputs.mjs \
      --input "$V7_INPUTS" \
      --output "$V7_OUTPUTS" \
      --error-output "$V7_ERRORS" \
      --model "$MODEL" \
      --concurrency "$V7_CONCURRENCY" \
      $LIMIT_ARG || true  # exit code 1 = some failures, acceptable
    echo "  v7 LLM summary batch complete."
  fi
  echo ""

  # ── Step A3: Generate source Excel A (pre-LLM-backfill) ────────────
  echo "── Step A3: Generate source Excel A (pre-LLM-backfill) ─────────"
  echo "  Building Excel from DB payload + v7 summaries..."
  $PYTHON -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from backend.analytics.phase09_idcevo_excel_report import generate_phase09_idcevo_excel_report
result = generate_phase09_idcevo_excel_report(
    db_path='$DB_PATH',
    output_path='$SOURCE_EXCEL_A_PRELLM',
    include_china_specific=True,
    llm_summaries_path='$V7_OUTPUTS',
)
print(f'  Generated: {result}')
"
  echo ""

  # ── Step A4: Backfill v7 LLM summaries into source Excel A ──────────
  echo "── Step A4: Backfill v7 LLM summaries ──────────────────────────"
  $PYTHON -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '$REPO_ROOT')
import openpyxl

REPO = Path('$REPO_ROOT')
EXCEL_IN = Path('$SOURCE_EXCEL_A_PRELLM')
EXCEL_OUT = Path('$SOURCE_EXCEL_A')
SUMMARIES = Path('$V7_OUTPUTS')

if not EXCEL_IN.exists():
    print(f'ERROR: {EXCEL_IN} not found', file=sys.stderr)
    sys.exit(1)

# Load summaries
summaries = {}
if SUMMARIES.exists():
    with open(SUMMARIES, encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            tid = str(r.get('ticket_id', '')).strip()
            source = str(r.get('source', '')).strip().lower()
            summary = str(r.get('summary', '')).strip()
            if tid and source == 'llm' and summary:
                summaries[tid] = {'summary': summary, 'model': str(r.get('model', '')).strip()}
print(f'  Loaded {len(summaries)} v7 LLM summaries')

wb = openpyxl.load_workbook(EXCEL_IN, data_only=True)
updated_total = 0
for ws in wb.worksheets:
    if ws.max_column < 16:
        continue
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1, max_col=47))]
    if not (header[0] == 'Ticket ID' and header[14] == 'LLM 处理摘要' and header[15] == '摘要来源'):
        continue
    updated = 0
    for row_idx in range(2, ws.max_row + 1):
        tid = str(ws.cell(row=row_idx, column=1).value or '').strip()
        if tid in summaries:
            ws.cell(row=row_idx, column=15).value = summaries[tid]['summary']
            model = summaries[tid]['model']
            ws.cell(row=row_idx, column=16).value = f'LLM / {model}' if model else 'LLM / deepseek-v4-pro'
            updated += 1
    print(f'  {ws.title}: updated {updated} rows')
    updated_total += updated

print(f'  Total updated: {updated_total}')
wb.save(EXCEL_OUT)
wb.close()
print(f'  Saved: {EXCEL_OUT}')
"
  echo ""

  # ── Step A5: Extract evidence sheet to source Excel B ────────────────
  echo "── Step A5: Extract evidence sheet → source Excel B ────────────"
  $PYTHON -c "
import sys
from pathlib import Path
sys.path.insert(0, '$REPO_ROOT')
import openpyxl

EXCEL_A = Path('$SOURCE_EXCEL_A')
EXCEL_B = Path('$SOURCE_EXCEL_B')

if not EXCEL_A.exists():
    print(f'ERROR: {EXCEL_A} not found', file=sys.stderr)
    sys.exit(1)

wb = openpyxl.load_workbook(EXCEL_A, data_only=True)

# Find the evidence sheet (named '08_证据原文' or similar)
evidence_ws = None
for ws in wb.worksheets:
    if '证据原文' in ws.title or 'evidence' in ws.title.lower():
        evidence_ws = ws
        break

if evidence_ws is None:
    print('ERROR: No evidence sheet found in source Excel A', file=sys.stderr)
    sys.exit(1)

print(f'  Found evidence sheet: {evidence_ws.title} ({evidence_ws.max_row} rows)')

# Copy to a new workbook
wb_b = openpyxl.Workbook()
ws_b = wb_b.active
ws_b.title = '06_证据原文'

for row in evidence_ws.iter_rows(values_only=True):
    ws_b.append(row)

wb_b.save(EXCEL_B)
wb_b.close()
wb.close()
print(f'  Saved: {EXCEL_B}')
"
  echo ""

  # Update export_v2_scenario_inputs.py to point at the new source Excel A
  # The script reads FULL_LLM_EXCEL — we need to make sure it finds the new file
  # We'll use a symlink or just let the V2 pipeline find the latest file
  echo "  Source Excels ready:"
  echo "    A: $SOURCE_EXCEL_A"
  echo "    B: $SOURCE_EXCEL_B"
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════
# PHASE B: V2 Pipeline — Source Excels → Final Excel
# ═══════════════════════════════════════════════════════════════════════

# Find the latest source Excel A if the date-based path doesn't exist
if [[ ! -f "$SOURCE_EXCEL_A" ]]; then
  SOURCE_EXCEL_A=$(ls -t "$EXPORT_DIR"/IDCEVO_Phase09_Closure_Risk_*_FullLLM_Complete.xlsx 2>/dev/null | head -1)
  if [[ -z "$SOURCE_EXCEL_A" ]]; then
    echo "ERROR: No source Excel A found in $EXPORT_DIR"
    exit 1
  fi
  echo "  Using latest source Excel A: $SOURCE_EXCEL_A"
fi

if [[ ! -f "$SOURCE_EXCEL_B" ]]; then
  echo "ERROR: Source Excel B not found: $SOURCE_EXCEL_B"
  exit 1
fi

# ── Step B1: Export V2 scenario extraction inputs ────────────────────
echo "── Step B1: Export V2 scenario extraction inputs ────────────────"
if [[ -f "$V2_INPUTS" ]]; then
  V2_INPUT_COUNT=$(wc -l < "$V2_INPUTS")
  echo "  $V2_INPUTS already exists ($V2_INPUT_COUNT records) — reusing."
  echo "  Delete it to force re-export."
else
  # Patch the Excel path in the export script at runtime
  $PYTHON -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from pathlib import Path
import exports.idcevo_phase09.llm.export_v2_scenario_inputs as mod
mod.FULL_LLM_EXCEL = Path('$SOURCE_EXCEL_A')
mod.EVIDENCE_EXCEL = Path('$SOURCE_EXCEL_B')
mod.OUTPUT_PATH = Path('$V2_INPUTS')
mod.main()
"
fi
echo ""

# ── Step B2: V2 LLM scenario extraction ──────────────────────────────
if [[ "$SKIP_V2_LLM" == "true" ]]; then
  echo "── Step B2: V2 LLM extraction SKIPPED (--skip-v2-llm) ──────────"
else
  echo "── Step B2: V2 LLM scenario extraction ────────────────────────"
  echo "  This may take ~1 hour for full 18k tickets at concurrency $V2_CONCURRENCY."
  $NODE scripts/extractPhase09Scenarios.mjs \
    --input "$V2_INPUTS" \
    --output "$V2_OUTPUTS" \
    --error-output "$V2_ERRORS" \
    --model "$MODEL" \
    --concurrency "$V2_CONCURRENCY" \
    --max-retries 3 \
    $LIMIT_ARG || true  # exit code 1 = some failures, acceptable
  echo ""
fi

# Deduplicate V2 outputs
echo "  Deduplicating V2 outputs..."
$PYTHON scripts/dedup_jsonl.py "$V2_OUTPUTS"
echo ""

# ── Step B3: Build the final Field Escape Scenario Library Excel ─────
echo "── Step B3: Build Field Escape Scenario Library Excel ───────────"
$PYTHON -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from pathlib import Path
import backend.analytics.phase09_idcevo_scenario_build as mod
mod.EXCEL_PATH = Path('$SOURCE_EXCEL_A')
mod.EXTRACTION_JSONL = Path('$V2_OUTPUTS')
mod.OUTPUT_XLSX = Path('$FINAL_XLSX')
mod.main()
"
echo ""

echo "════════════════════════════════════════════════════════════════════"
echo "  Pipeline complete!"
echo "  Final Excel: $FINAL_XLSX"
echo "════════════════════════════════════════════════════════════════════"
