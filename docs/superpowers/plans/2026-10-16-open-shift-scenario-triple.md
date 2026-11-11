# Open Shift Scenario Triple Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a standalone V12-style Excel, JSON knowledge base, and static HTML catalog for open Shift tickets, showing customer symptoms by Service Function and their ticket-recorded future resolution evidence.

**Architecture:** The builder will load the 902 open-shift candidates and fingerprint-validated v2 LLM summaries, enrich them with Octane function metadata, and reuse the Phase 09 Service Function classifier. It will cluster records by Service Function plus normalized symptom, retain a separate future-resolution evidence leaf, and label every plan as recorded evidence rather than verified repair.

**Tech Stack:** Python 3.13, SQLite, openpyxl, JSON, static HTML, existing Phase 09 Service Function classifier.

---

### Task 1: Build Full Source Records

**Files:**
- Create: `backend/analytics/open_shift_scenario_triple.py`
- Test: `backend/tests/test_open_shift_scenario_triple.py`

- [ ] **Step 1: Write the failing test**

```python
def test_load_open_shift_full_records_keeps_validated_llm_and_function_metadata(tmp_path):
    records = load_open_shift_full_records(...)
    assert records[0]["service_function"] == "Navigation"
    assert records[0]["future_resolution_status"] == "Recorded future resolution plan - not verified repair"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_open_shift_scenario_triple.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
def load_open_shift_full_records(...):
    records = select_open_shift_ticket_records(load_open_ticket_records(db_path))
    summaries = load_validated_llm_summaries(...)
    return [enrich_with_function_metadata(record, summaries[record["ticket_id"]]) for record in records]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_open_shift_scenario_triple.py -q`

Expected: PASS.

### Task 2: Cluster Symptoms and Future Resolution Plans

**Files:**
- Modify: `backend/analytics/open_shift_scenario_triple.py`
- Modify: `backend/tests/test_open_shift_scenario_triple.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_scenarios_groups_same_service_and_symptom_but_preserves_future_plan_evidence():
    scenarios = build_shift_scenarios([...])
    assert scenarios[0]["ticket_count"] == 2
    assert scenarios[0]["future_resolution_status"] == "Recorded future resolution plan - not verified repair"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_open_shift_scenario_triple.py -q`

Expected: FAIL because scenario clustering is not implemented.

- [ ] **Step 3: Write minimal implementation**

```python
def build_shift_scenarios(records):
    clusters = group_by_service_function_and_normalized_symptom(records)
    return [build_scenario(cluster) for cluster in clusters]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_open_shift_scenario_triple.py -q`

Expected: PASS.

### Task 3: Render the Source Workbook and Artifact Triple

**Files:**
- Modify: `backend/analytics/open_shift_scenario_triple.py`
- Create: `scripts/build_open_shift_scenario_triple.py`
- Modify: `backend/tests/test_open_shift_scenario_triple.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_open_shift_artifact_triple_writes_synchronized_excel_json_and_html(tmp_path):
    result = build_open_shift_artifact_triple(...)
    assert result["xlsx"].exists()
    assert result["json"].exists()
    assert result["html"].exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_open_shift_scenario_triple.py -q`

Expected: FAIL because the artifact writer is not implemented.

- [ ] **Step 3: Write minimal implementation**

```python
build_open_shift_source_workbook(full_records, source_xlsx)
build_open_shift_v12_style_workbook(scenarios, source_xlsx, triple_xlsx)
write_knowledge_base(scenarios, triple_json)
write_catalog_html(payload, triple_html)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_open_shift_scenario_triple.py -q`

Expected: PASS.

### Task 4: Generate and Validate Candidate Release

**Files:**
- Create: `exports/open_shift_tickets/IDCEVO_Open_Shift_Tickets_20261015_FullLLM.xlsx`
- Create: `exports/open_shift_tickets/IDCEVO_Open_Shift_Scenario_Library_V12_Symptom_to_Future_Resolution_Trees.xlsx`
- Create: `exports/open_shift_tickets/IDCEVO_Open_Shift_Scenario_Library_V12_Symptom_to_Future_Resolution_Trees_Decision_Tree_Knowledge_Base.json`
- Create: `exports/open_shift_tickets/IDCEVO_Open_Shift_Scenario_Library_V12_Symptom_to_Future_Resolution_Trees_Decision_Tree_Catalog.html`

- [ ] **Step 1: Run the focused test suite**

Run: `python -m pytest backend/tests/test_open_shift_scenario_triple.py -q`

Expected: PASS.

- [ ] **Step 2: Generate all four artifacts from one builder invocation**

Run: `python scripts/build_open_shift_scenario_triple.py --db-path database/source/qgate_raw.db --inputs exports/open_shift_tickets/open_shift_ticket_inputs_20261015_v2.jsonl --summaries exports/open_shift_tickets/open_shift_ticket_summaries_20261015_v2.jsonl --output-directory exports/open_shift_tickets`

Expected: The source workbook plus Excel/JSON/HTML triple are written with matching scenario and ticket counts.

- [ ] **Step 3: Validate the triple and visually inspect HTML**

Run: `python -m pytest backend/tests/test_open_shift_scenario_triple.py -q`

Expected: PASS.

Run: use browser automation to open the generated HTML and verify a populated Service Function, a future-resolution leaf, and a zero/low-volume function render without clipping.