# Open Ticket Postponement Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducible Excel and CSV report for open Octane tickets whose planned target release is later than their reported software, including bounded, evidence-grounded LLM explanations.

**Architecture:** A Python analytics module will normalize open-ticket history into source/target release comparisons, delivery-plan evidence, and LLM input JSONL. A separate Node runner will request one structured postponement summary per ticket using the existing company chat gateway. The Python exporter will validate matching fingerprints and render each classification sheet with source/target software, delivery evidence, and the structured summary.

**Tech Stack:** Python 3.11, SQLite, openpyxl, Node.js, Vitest, existing company chat gateway.

---

### Task 1: Normalize Ticket and Delivery Evidence

**Files:**
- Create: `backend/analytics/open_ticket_postponement.py`
- Test: `backend/tests/test_open_ticket_postponement.py`

- [ ] **Step 1: Write the failing test**

```python
def test_classify_open_ticket_requires_a_strictly_later_confirmed_target_release():
    record = classify_ticket(
        software_version="idcevo-pu2707-27w01.1-1",
        target_i_step="NA05-27-11-460",
    )
    assert record["classification"] == "confirmed_postponed"
    assert record["postponed_release_steps"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_open_ticket_postponement.py -q`

Expected: FAIL because `backend.analytics.open_ticket_postponement` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
def classify_ticket(*, software_version: str, target_i_step: str) -> dict[str, object]:
    source = parse_release(software_version)
    target = parse_release(target_i_step)
    if source and target and release_index(target) > release_index(source):
        return {"classification": "confirmed_postponed", "postponed_release_steps": release_index(target) - release_index(source)}
    return {"classification": "not_comparable", "postponed_release_steps": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_open_ticket_postponement.py -q`

Expected: PASS.

### Task 2: Build Bounded LLM Inputs and Workbook Rows

**Files:**
- Modify: `backend/analytics/open_ticket_postponement.py`
- Modify: `backend/tests/test_open_ticket_postponement.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_inputs_includes_delivery_evidence_and_sanitizes_html():
    record = build_ticket_record(...)
    input_record = build_llm_input_record(record)
    assert input_record["ticket"]["target_i_step"] == "NA05-27-11-460"
    assert "<html" not in input_record["ticket"]["postponement_document_evidence"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_open_ticket_postponement.py -q`

Expected: FAIL because bounded evidence input is not implemented.

- [ ] **Step 3: Write minimal implementation**

```python
def build_llm_input_record(record: Mapping[str, object]) -> dict[str, object]:
    ticket = {
        "title": clean_text(record["name"]),
        "problem_evidence": bounded_text(record["description"], 4500),
        "target_i_step": record["target_i_step"],
        "postponement_document_evidence": bounded_text(record["postponement_document"], 1600),
    }
    return with_fingerprint(record["ticket_id"], ticket)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_open_ticket_postponement.py -q`

Expected: PASS.

### Task 3: Request Structured Open-Ticket LLM Summaries

**Files:**
- Create: `server/openTicketPostponementLlmRunner.mjs`
- Create: `scripts/summarizeOpenTicketPostponementInputs.mjs`
- Test: `src/test/server/openTicketPostponementLlmRunner.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
it("returns only evidence-grounded Chinese postponement fields", async () => {
  const summary = parseOpenTicketPostponementResponse('{"issue_summary":"...","postponement_assessment":"...","review_focus":"..."}');
  expect(summary.issue_summary).toBeTruthy();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/test/server/openTicketPostponementLlmRunner.test.ts`

Expected: FAIL because the runner module does not exist.

- [ ] **Step 3: Write minimal implementation**

```js
export async function summarizeOpenTicketPostponement({ ticketInput, model, requestCompletion }) {
  const response = await requestCompletion({ model, messages: buildOpenTicketPostponementMessages([ticketInput]) });
  return buildSummary(ticketInput, parseOpenTicketPostponementResponse(response.content));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- src/test/server/openTicketPostponementLlmRunner.test.ts`

Expected: PASS.

### Task 4: Render the Enriched Report and Validate It

**Files:**
- Create: `scripts/export_open_ticket_postponement_report.py`
- Modify: `backend/tests/test_open_ticket_postponement.py`

- [ ] **Step 1: Write the failing test**

```python
def test_report_row_exposes_source_target_and_llm_assessment():
    row = workbook_row(record, llm_summary)
    assert row["Target Software / I-Step"] == "NA05-27-11-460"
    assert row["LLM Postponement Assessment"] == "..."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_open_ticket_postponement.py -q`

Expected: FAIL because workbook-row rendering is not implemented.

- [ ] **Step 3: Write minimal implementation**

```python
write_report(
    records,
    llm_summaries_path="exports/open_ticket_postponement/summaries.jsonl",
    output_path="exports/Open_Ticket_Postponement_20261015_LLM.xlsx",
)
```

- [ ] **Step 4: Run focused tests and export**

Run: `python -m pytest backend/tests/test_open_ticket_postponement.py -q`

Expected: PASS.

Run: `python scripts/export_open_ticket_postponement_report.py --db-path database/source/qgate_raw.db --inputs-output exports/open_ticket_postponement/inputs.jsonl --output exports/Open_Ticket_Postponement_20261015_LLM.xlsx`

Expected: Workbook contains all classification sheets and summary fields.

### Task 5: Produce and Verify Summaries

**Files:**
- Create: `exports/open_ticket_postponement/inputs.jsonl`
- Create: `exports/open_ticket_postponement/summaries.jsonl`
- Create: `exports/Open_Ticket_Postponement_20261015_LLM.xlsx`
- Create: `exports/Open_Ticket_Confirmed_Postponed_20261015_LLM.csv`

- [ ] **Step 1: Run the resumable LLM runner**

Run: `node scripts/summarizeOpenTicketPostponementInputs.mjs --input exports/open_ticket_postponement/inputs.jsonl --output exports/open_ticket_postponement/summaries.jsonl --model deepseek-v4-flash --concurrency 10 --max-retries 3`

Expected: One validated summary per input where the configured gateway is available.

- [ ] **Step 2: Regenerate the report with matching summaries**

Run: `python scripts/export_open_ticket_postponement_report.py --db-path database/source/qgate_raw.db --inputs-output exports/open_ticket_postponement/inputs.jsonl --llm-summaries exports/open_ticket_postponement/summaries.jsonl --output exports/Open_Ticket_Postponement_20261015_LLM.xlsx --csv-output exports/Open_Ticket_Confirmed_Postponed_20261015_LLM.csv`

Expected: Each ticket row exposes full source/target software, delivery-plan evidence, a summary source, and three LLM assessment fields.

- [ ] **Step 3: Reopen the workbook and validate**

Run: `python -c "from openpyxl import load_workbook; wb = load_workbook('exports/Open_Ticket_Postponement_20261015_LLM.xlsx', read_only=True); print(wb.sheetnames)"`

Expected: `00_Definitions`, `01_Confirmed_Postponed`, `02_Requested_Target_Review`, `03_Same_Target_Not_Postponed`, `04_Earlier_Target_Data_Check`, and `05_Manual_Version_Comparison`.