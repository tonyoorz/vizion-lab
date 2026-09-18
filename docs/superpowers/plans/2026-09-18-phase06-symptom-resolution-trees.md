# Phase 06 Symptom-to-Resolution Evidence Trees Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a Phase 06 tree catalog with the same navigation shape as Phase 09 V12 while preserving the distinction between Ticket-recorded resolution evidence and unapproved LLM RCA candidates.

**Architecture:** A new standalone builder will convert one Phase 06 Ticket into one navigation record, map it through the existing customer-facing Service Function taxonomy, then reuse the V12 hierarchy of Issue Group, Symptom Pattern, Trigger Pattern, and terminal evidence. Terminal text will come only from raw repair-plan or decision evidence and will be named `Recorded Resolution Evidence`. The Phase 06 RCA candidate library contributes P1/P2 review counts and source references, never root-cause rules or automatic repair instructions.

**Tech Stack:** Python 3.13, OpenPyXL, JSON, static HTML, pytest, existing Phase 09 Service Function taxonomy and V12 tree utilities.

---

## Task 1: Convert Phase 06 Tickets into V12-Shaped Navigation Records

**Files:**

- Create: `backend/analytics/phase06_symptom_resolution_tree_build.py`
- Create: `backend/tests/test_phase06_symptom_resolution_tree_build.py`

- [x] **Step 1: Write a failing record-conversion test**

```python
def test_build_phase06_tree_records_uses_recorded_resolution_evidence_only() -> None:
    records = build_phase06_tree_records([PHASE06_AUDIT_RECORD])

    assert records[0]["customer_symptom"] == "Projection connection fails"
    assert records[0]["recorded_resolution_evidence"] == "Repair plan evidence excerpt"
    assert records[0]["recovery_action"] == "Repair plan evidence excerpt"
    assert records[0]["llm_root_cause_assessment"] == ""
```

The fixture must include an LLM root-cause assessment and repair-plan evidence. The test proves that the tree payload uses only the recorded source evidence in its terminal text.

- [x] **Step 2: Run the focused test and verify red state**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -m pytest backend/tests/test_phase06_symptom_resolution_tree_build.py -q
```

Expected: collection fails because `build_phase06_tree_records` does not exist.

- [x] **Step 3: Implement record conversion**

```python
def build_phase06_tree_records(
    audit_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build V12-shaped Ticket navigation records without materializing RCA claims."""
```

Use `solution_cluster` or `function` as the Service Function mapping context, `ticket_title` as the recorded symptom, and `problem_description_excerpt` as recorded trigger/observed context. Derive issue, symptom, and trigger pattern only with existing Phase 09 classifiers. Copy at most one concise source excerpt from `repair_plan_evidence` or `decision_evidence` to `recorded_resolution_evidence`; do not copy LLM root-cause or fix-quality text to that field.

- [x] **Step 4: Run the focused test and verify green state**

Run the command from Step 2.

Expected: a source-evidence terminal exists and LLM RCA text is excluded from it.

## Task 2: Build and Annotate Phase 06 Navigation Trees

**Files:**

- Modify: `backend/analytics/phase06_symptom_resolution_tree_build.py`
- Modify: `backend/tests/test_phase06_symptom_resolution_tree_build.py`

- [x] **Step 1: Write a failing tree contract test**

```python
def test_build_phase06_symptom_resolution_trees_has_v12_layers_without_rca_rules() -> None:
    trees, nodes = build_phase06_symptom_resolution_trees(PHASE06_TREE_RECORDS)

    carplay_nodes = [node for node in nodes if node["tree_id"] == "DT-CARPLAY"]
    assert {node["node_type"] for node in carplay_nodes} == {
        "Entry", "Issue Group", "Symptom Pattern", "Trigger Pattern",
        "Recorded Resolution Evidence",
    }
    resolution = next(node for node in carplay_nodes if node["node_type"] == "Recorded Resolution Evidence")
    assert resolution["resolution_status"] == "ticket_recorded_resolution_evidence"
    assert resolution["approved_tree_rule"] is False
    assert "root cause" not in resolution["question_or_action"].casefold()
```

- [x] **Step 2: Run the focused test and verify red state**

Run the command from Task 1, Step 2.

Expected: collection or test failure because the tree builder does not exist.

- [x] **Step 3: Implement tree composition and RCA review metadata**

Use `build_symptom_solution_trees(records, [])` for the V12 hierarchy, then rename only terminal nodes from `Ticket Solution` to `Recorded Resolution Evidence`. Tree and node metadata must state:

- resolution text is an excerpt from repair-plan or decision evidence;
- blank terminal text means no recorded resolution evidence;
- P1/P2 counts are review metadata only;
- `approved_tree_rule` remains false everywhere.

The generated navigation node must retain source Ticket IDs and aggregate P1/P2/no-fix counts from its supporting records.

- [x] **Step 4: Run focused tests and verify green state**

Run the command from Task 1, Step 2.

Expected: V12-shaped Phase 06 nodes exist, but no node is an approved RCA or repair rule.

## Task 3: Export a Phase 06 Tree Catalog

**Files:**

- Modify: `backend/analytics/phase06_symptom_resolution_tree_build.py`
- Modify: `backend/tests/test_phase06_symptom_resolution_tree_build.py`

- [x] **Step 1: Write a failing export test**

```python
def test_export_phase06_tree_catalog_writes_excel_json_and_html(tmp_path) -> None:
    export_phase06_symptom_resolution_tree_catalog(
        catalog=SYNTHETIC_CATALOG,
        output_xlsx=tmp_path / "phase06-tree.xlsx",
        output_json=tmp_path / "phase06-tree.json",
        output_html=tmp_path / "phase06-tree.html",
    )

    workbook = load_workbook(tmp_path / "phase06-tree.xlsx", data_only=True)
    assert "05_Decision_Tree_Nodes" in workbook.sheetnames
    assert "Recorded Resolution Evidence" in (tmp_path / "phase06-tree.html").read_text(encoding="utf-8")
    assert "No automatic RCA or repair rules" in (tmp_path / "phase06-tree.json").read_text(encoding="utf-8")
```

- [x] **Step 2: Run the export test and verify red state**

Run the command from Task 1, Step 2.

Expected: failure because the export function does not exist.

- [x] **Step 3: Implement Excel, JSON, HTML, and CLI output**

Create these Excel sheets:

- `00_Overview`: source counts, V12-shaped navigation policy, and zero automatic RCA/repair rules.
- `01_Phase06_Ticket_Library`: one row per Phase 06 Ticket with mapped navigation fields and raw evidence references.
- `02_RCA_Review_Queue`: P1/P2 review candidates copied from the existing candidate library contract.
- `03_Service_Function_Catalog`: Service Function tree coverage.
- `04_Decision_Tree_Catalog`: one V12-shaped catalog tree per Service Function.
- `05_Decision_Tree_Nodes`: tree nodes with resolution status, P1/P2/no-fix counts, and source Ticket IDs.
- `06_Source_Contract`: field and promotion rules.

The JSON must declare a distinct Phase 06 schema and explicitly say `Recorded Resolution Evidence` is copied from repair-plan or decision evidence, not a verified solution. The HTML must match the V12 navigation layout, use a Service Function selector, show `Recorded Resolution Evidence` leaves, and expose P1/P2 counts only as human-review metadata.

Add CLI options for workbook, input cache, summary cache, and output XLSX/JSON/HTML paths. Default outputs must be new Phase 06 V1 files and must never overwrite Phase 09 V12.

- [x] **Step 4: Run focused tests, compilation, and browser validation**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -W error::SyntaxWarning -m pytest backend/tests/test_phase06_symptom_resolution_tree_build.py -q
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -W error::SyntaxWarning -m py_compile backend/analytics/phase06_symptom_resolution_tree_build.py
```

Use Playwright to select a populated Service Function, confirm terminal leaves and review metadata render, and test the catalog at desktop and mobile widths.

## Task 4: Generate the Real Phase 06 V12-Shaped Catalog

**Files:**

- Create: `docs/phase06-symptom-resolution-trees.md`
- Generate: `exports/idcevo_phase06/IDCEVO_Phase06_Symptom_to_Resolution_Evidence_Trees_V1.xlsx`
- Generate: `exports/idcevo_phase06/IDCEVO_Phase06_Symptom_to_Resolution_Evidence_Trees_V1_Decision_Tree_Knowledge_Base.json`
- Generate: `exports/idcevo_phase06/IDCEVO_Phase06_Symptom_to_Resolution_Evidence_Trees_V1_Decision_Tree_Catalog.html`

- [x] **Step 1: Document source and promotion boundaries**

Document that Phase 06 Ticket titles and description excerpts support navigation patterns; raw repair-plan/decision excerpts support terminal evidence only; P1/P2 and LLM assessments remain candidate-review metadata; no Phase 06 output automatically creates RCA or repair rules.

- [x] **Step 2: Generate from the supplied Phase 06 source workbook**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe backend/analytics/phase06_symptom_resolution_tree_build.py --workbook "C:\Users\q446328\OneDrive - BMW Group\defect analysis\Phase06full.xlsx"
```

Expected: three new Phase 06 V1 artifacts are written with no network or LLM call.

- [x] **Step 3: Verify the final artifact contract**

Verify Excel/JSON node counts agree, every `approved_tree_rule` is false, `Recorded Resolution Evidence` has only source-recorded text, P1/P2 remain review metadata, and no Phase 09 V12 file is overwritten.

## Review Checklist

- [x] The tree is structurally comparable to Phase 09 V12 but uses a distinct Phase 06 source policy.
- [x] The terminal node never presents LLM root-cause analysis as a recorded resolution or repair instruction.
- [x] The terminal node label is `Recorded Resolution Evidence`, not `Known Solution` or `Verified Repair`.
- [x] Every RCA-related count is explicitly human-review metadata.
- [x] Phase 09 V12 artifacts remain unchanged.
