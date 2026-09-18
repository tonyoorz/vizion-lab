# Phase 06 RCA Candidate Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate an evidence-cited, human-reviewable Phase 06 RCA and remedy candidate library without treating LLM analysis as an approved diagnostic or repair rule.

**Architecture:** A new standalone Python builder reads the Phase 06 detail workbook plus the cached input and summary JSONL files. It accepts only summary-side `source=llm` records whose Ticket ID, input fingerprint, and input version match one unambiguous cached input record. It writes a review queue, full audit rows, function readiness aggregates, a JSON index, and a static review page; no output record is automatically promoted into a decision-tree branch.

**Tech Stack:** Python 3.13, OpenPyXL, JSONL, static HTML, pytest.

---

## Task 1: Parse and Validate the Phase 06 Source Contract

**Files:**

- Create: `backend/analytics/phase06_rca_candidate_library.py`
- Create: `backend/tests/test_phase06_rca_candidate_library.py`

- [x] **Step 1: Write failing tests for a valid cache join and a rejected fingerprint mismatch**

```python
def test_load_validated_summaries_requires_matching_input_fingerprint(tmp_path) -> None:
    inputs_path = write_jsonl(tmp_path / "inputs.jsonl", [{
        "ticket_id": "D-1", "input_fingerprint": "source-a",
        "input_version": "phase06-llm-summary-v1", "ticket": {},
    }])
    summaries_path = write_jsonl(tmp_path / "summaries.jsonl", [{
        "ticket_id": "D-1", "input_fingerprint": "other-source",
        "input_version": "phase06-llm-summary-v1", "source": "llm",
        "model": "test", "summary": VALID_PHASE06_SUMMARY,
    }])

    validated, audit = load_validated_phase06_summaries(inputs_path, summaries_path)

    assert validated == {}
    assert audit["unmatched_or_invalid_summary_count"] == 1
```

- [x] **Step 2: Run the focused test and verify red state**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m pytest backend/tests/test_phase06_rca_candidate_library.py -q
```

Expected: collection fails because `load_validated_phase06_summaries` does not exist.

- [x] **Step 3: Implement bounded JSONL loading and five-section parsing**

```python
PHASE06_SUMMARY_LABELS = (
    "根因：", "修复质量：", "Reject/关闭原因：", "后续闭环：", "风险判断与逃逸回顾：",
)

def parse_phase06_summary(summary: str) -> dict[str, str]:
    positions = sorted(
        (summary.find(label), label)
        for label in PHASE06_SUMMARY_LABELS
        if summary.find(label) >= 0
    )
    return {
        label: summary[start + len(label): end].strip()
        for index, (start, label) in enumerate(positions)
        for end in [positions[index + 1][0] if index + 1 < len(positions) else len(summary)]
    }
```

`load_validated_phase06_summaries()` must accept a summary only when summary-side `source == "llm"` case-insensitively, the Ticket ID exists exactly once in the input cache, and both fingerprint and input version match exactly. Labels must occur once at line start and in generated order. It must report invalid, unmatched, missing-label, malformed, and duplicate records separately.

- [x] **Step 4: Run the focused test and verify green state**

Run the command from Step 2.

Expected: the valid join survives and the mismatch is excluded.

## Task 2: Derive Review Candidates Without Automatic RCA Claims

**Files:**

- Modify: `backend/analytics/phase06_rca_candidate_library.py`
- Modify: `backend/tests/test_phase06_rca_candidate_library.py`

- [x] **Step 1: Write failing tests for conservative candidate states**

```python
def test_build_candidate_marks_llm_causal_text_for_human_review_only() -> None:
    candidate = build_rca_candidate_record(
        detail_record=DETAIL_RECORD_WITH_REPAIR_PLAN,
        summary_record=VALIDATED_SUMMARY_WITH_CAUSAL_MECHANISM,
    )

    assert candidate["review_priority"] == "P1"
    assert candidate["tree_rule_status"] == "human_review_required"
    assert candidate["approved_tree_rule"] is False
    assert candidate["raw_remedy_evidence_present"] is True
```

Add an adjacent test where a root-cause segment contains `未提供` or `无法确认`; it must become `P2` or `Not shortlisted`, never `P1` from the LLM signal alone.

- [x] **Step 2: Run the focused tests and verify red state**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m pytest backend/tests/test_phase06_rca_candidate_library.py -q
```

Expected: failure because the candidate builder does not exist.

- [x] **Step 3: Implement review-only classifications and aggregates**

```python
def build_rca_candidate_record(
    detail_record: Mapping[str, Any],
    summary_record: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return an auditable review candidate; never approve a tree rule automatically."""
```

The result must retain: Ticket ID, function and Solution Cluster fields, title and description excerpt, evidence index, raw repair-plan/decision/no-fix evidence, parsed LLM root-cause and fix-quality assessments, source model, and all Boolean signal fields.

Set `tree_rule_status` to `human_review_required` for every candidate. Set `approved_tree_rule` to `False` for every candidate. Use the following review priorities:

- `P1`: an LLM causal-mechanism signal plus recorded repair-plan or decision evidence, a raw evidence reference, and neither root-cause uncertainty nor no-fix evidence.
- `P2`: a causal-mechanism signal or recorded remedy evidence that is incomplete, uncertain, or lacks the other required evidence.
- `Not shortlisted`: neither a causal-mechanism signal nor recorded remedy evidence.

`build_phase06_rca_candidate_library()` must return full audit records, a queue containing only P1/P2 records, readiness aggregates per function, and explicit summary counts. It must not cluster or promote text into a decision rule.

- [x] **Step 4: Run focused tests and verify green state**

Run the command from Step 2.

Expected: all candidate-state tests pass and no test permits an auto-approved rule.

## Task 3: Export Review Artifacts

**Files:**

- Modify: `backend/analytics/phase06_rca_candidate_library.py`
- Modify: `backend/tests/test_phase06_rca_candidate_library.py`

- [x] **Step 1: Write a failing temporary-workbook export test**

```python
def test_export_library_writes_auditable_review_sheets_json_and_html(tmp_path) -> None:
    export_phase06_rca_candidate_library(
        library=SYNTHETIC_LIBRARY,
        output_xlsx=tmp_path / "phase06-rca.xlsx",
        output_json=tmp_path / "phase06-rca.json",
        output_html=tmp_path / "phase06-rca.html",
    )

    workbook = load_workbook(tmp_path / "phase06-rca.xlsx", data_only=True)
    assert workbook.sheetnames == [
        "00_Overview", "01_RCA_Review_Queue", "02_All_Ticket_Audit",
        "03_Function_Readiness", "04_Data_Dictionary",
    ]
    assert "human_review_required" in (tmp_path / "phase06-rca.json").read_text(encoding="utf-8")
    assert "No automatic decision-tree rules" in (tmp_path / "phase06-rca.html").read_text(encoding="utf-8")
```

- [x] **Step 2: Run the export test and verify red state**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m pytest backend/tests/test_phase06_rca_candidate_library.py -q
```

Expected: failure because the exporter does not exist.

- [x] **Step 3: Implement XLSX, JSON, HTML, and CLI output**

Create these Excel sheets:

- `00_Overview`: source contract, validation counts, P1/P2 counts, and `Approved tree rules = 0`.
- `01_RCA_Review_Queue`: only P1/P2 candidates, including raw-evidence fields and review rationale.
- `02_All_Ticket_Audit`: all source detail records with validation and eligibility states.
- `03_Function_Readiness`: per-function counts for valid LLM summaries, mechanism signals, uncertainty, recorded remedy evidence, P1/P2 queue, and approved rules.
- `04_Data_Dictionary`: each status and the rule that produced it.

The JSON payload must use `schema_version: "phase06-rca-candidate-library-v1"` and state that LLM assessments are candidates, Ticket evidence is required for review, and zero rules are automatically approved. The HTML must be a compact review surface with function/priority filters, a promotion-path diagram, and source evidence/table fields; it must never present a candidate as an executable diagnostic decision.

Add CLI options:

```text
--workbook <Phase06full.xlsx or local Resolution_Quality.xlsx>
--inputs <phase06_inputs_v1.jsonl>
--summaries <phase06_summaries_v1_pro.jsonl>
--output-xlsx <path>
--output-json <path>
--output-html <path>
```

- [x] **Step 4: Run the focused tests and strict syntax check**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -W error::SyntaxWarning -m pytest backend/tests/test_phase06_rca_candidate_library.py -q
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -W error::SyntaxWarning -m py_compile backend/analytics/phase06_rca_candidate_library.py
```

Expected: all tests pass and compilation emits no warnings.

## Task 4: Generate the Actual Phase 06 Candidate Library and Document Governance

**Files:**

- Create: `docs/phase06-rca-candidate-library.md`
- Generate: `exports/idcevo_phase06/IDCEVO_Phase06_RCA_Candidate_Library_V1.xlsx`
- Generate: `exports/idcevo_phase06/IDCEVO_Phase06_RCA_Candidate_Library_V1.json`
- Generate: `exports/idcevo_phase06/IDCEVO_Phase06_RCA_Candidate_Library_V1.html`

- [x] **Step 1: Document the evidence contract**

State that `Phase06full.xlsx` supplies Ticket facts, raw evidence indexes, and repair/decision evidence; the LLM cache supplies candidate assessments only after exact fingerprint/version validation. State that `Resolution evidence complete` is process evidence, not automatic proof of technical RCA or validated remedy.

- [x] **Step 2: Generate artifacts from the supplied Phase 06 workbook**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe backend/analytics/phase06_rca_candidate_library.py --workbook "C:\Users\q446328\OneDrive - BMW Group\defect analysis\Phase06full.xlsx"
```

Expected: V1 Excel, JSON, and HTML artifacts are written under `exports/idcevo_phase06/` without network or LLM calls.

- [x] **Step 3: Verify artifact invariants and visual behavior**

Verify that the workbook and JSON agree on all/audit queue counts; validate no candidate has `approved_tree_rule: true`; confirm the HTML shows the human-review restriction and supports filtering. Use Playwright at desktop and mobile widths before describing the artifact as complete.

## Review Checklist

- [x] The builder never turns an LLM causal statement into a verified RCA fact.
- [x] Every queue row retains Ticket ID, evidence index, and raw remedy/decision evidence fields.
- [x] Missing, duplicate, mismatched, fallback, and malformed summaries remain auditable instead of silently joining.
- [x] The export preserves V11 Phase 09 artifacts unchanged.
- [x] No generated candidate is automatically promoted to a decision-tree rule.
