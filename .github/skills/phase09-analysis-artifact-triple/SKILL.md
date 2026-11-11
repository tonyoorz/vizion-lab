---
name: phase09-analysis-artifact-triple
description: "Generate or refresh a governed Phase 09 analysis artifact triple: Excel workbook, machine-readable JSON knowledge base, and static HTML catalog. Use when the user asks to generate analysis three-piece artifacts, regenerate V12, sync Excel JSON HTML, rebuild a Phase 09 decision-tree or scenario-analysis deliverable, validate a backup snapshot, or publish an updated analysis catalog."
argument-hint: "[full rebuild | approved snapshot | output stem]"
user-invocable: true
---

# Phase 09 Analysis Artifact Triple

Produce one consistent, auditable **Phase 09 / V12** analysis release consisting of:

1. An Excel workbook for review and filtering.
2. A JSON knowledge base for machines and downstream tools.
3. A standalone HTML catalog for interactive inspection.

This skill applies only to the repository's Phase 09 / V12 scenario and
decision-tree artifacts. It is not a generic spreadsheet export workflow and
must not be used as the release procedure for Phase 06, QGate, V13, or unrelated
Vizion Lab reports.

## When to Use

Use this skill when the user asks to:

- Generate or regenerate the Phase 09 Excel + JSON + HTML three-piece set.
- Refresh V12 or a versioned scenario/decision-tree analysis.
- Add or split Service Functions, Function Map features, source-evidence states,
  or display metadata and publish the result consistently.
- Rebuild an artifact from a known full snapshot or approved backup.
- Validate whether a backup is safe to use as an input baseline.
- Synchronize a verified candidate triple back to an original artifact stem.

Do not use this skill to generate V13 or a new analysis generation unless the
user explicitly asks for that version.

## Operating Contract

- Treat Excel, JSON, and HTML as one release. Never update only one member and
  describe the release as synchronized.
- Use one builder invocation to create all three outputs from the same in-memory
  scenario set.
- Keep the selected input snapshot immutable. Do not modify source workbooks,
  approved backups, raw Ticket data, or LLM caches while generating artifacts.
- Preserve evidence boundaries: Ticket recovery text is not an approved repair;
  RCA text is not a verified root cause unless a separate verified source proves it.
- Do not silently collapse a newly requested Function Map feature into HMI,
  Navigation, Entertainment, Speech, or another coarse Service Function.
- Never overwrite a locked Excel file. Generate and validate a candidate triple
  first, then replace all three original artifacts only after the lock is released.
- A release claiming current/live evidence must pass per-ticket source completeness/freshness and
  canonical/cache/raw-JSON consistency gates before LLM work or artifact writes.
  A populated DB and a recent `max(fetched_at)` are insufficient. Follow the
  [current source evidence contract](../phase09-ticket-to-aftersales-decision-tree/references/lineage-and-release-contract.md#current-source-evidence-contract).
- Explicitly labeled offline database-snapshot rebuilding instead uses the
  [snapshot evidence contract](../phase09-ticket-to-aftersales-decision-tree/references/lineage-and-release-contract.md#database-snapshot-evidence-contract): immutable scope/content and matching model inputs, no required online repair artifacts, and no claimed live-source coverage.
- Separate historical Shift/Tolerate evidence, current postponement, repair
  progress and delivery progress. Old workbook summaries, keyword co-occurrence,
  Phase 05/08 and planned versions do not establish current verified repair.
- Preserve unavailable tickets as explicit historical-only rows, not successful
  empty fetches. Recheck the model transport budget as well as the input
  fingerprint; silently truncated structured JSON invalidates current analysis.

## Inputs and Outputs

For V12 symptom-to-solution artifacts, the normal builder is:

- [scenario builder](../../../backend/analytics/phase09_idcevo_scenario_build.py)

The builder's release outputs share one stem:

```text
<stem>.xlsx
<stem>_Decision_Tree_Catalog.html
<stem>_Decision_Tree_Knowledge_Base.json
```

Before building, explicitly record:

- Input mode: `full rebuild` or `approved snapshot`.
- Exact source workbook, JSONL/cache, and/or approved backup paths.
- Exact output stem and directory.
- Whether the existing release may be overwritten. Ask the user on every
  release; do not assume a default overwrite policy.
- The intended artifact version. Do not infer a new version name.

## Procedure

### 1. Establish the Authoritative Input

Choose one branch before editing or generating.

**Full rebuild**

- Use the current Phase 09 extraction JSONL and metadata workbook consumed by
  `load_extractions()` and `load_excel_metadata()`.
- Use this when classification or builder logic changed and the user wants the
  result recalculated from source data.

**Approved snapshot / backup**

- Use the exact Excel, JSON, and HTML paths supplied or approved by the user.
- Validate them before using them as a baseline:
  - Excel `01_Scenario_Library` nonblank row count matches the sum of JSON
    `decision_trees[].scenario_count`.
  - JSON schema version is expected for the release.
  - The sum of `decision_trees[].ticket_count` is plausible for the snapshot.
  - Excel Service Function names resolve to JSON Service Function IDs.
- Reject inconsistent snapshots. Do not silently build a reduced release.

### 2. Define the Change and Test It First

Anchor the requested behavior in a nearby builder test, usually:

- [scenario builder tests](../../../backend/tests/test_phase09_idcevo_scenario_build.py)

Examples of testable contracts:

- A named Function Map feature has an independent `service_function_id`.
- A more precise feature alias wins over a coarse category, while CarPlay and
  HiCar retain their explicit higher-priority classification.
- Source Ticket evidence is distinct from retained field scenarios:
  - `field_scenario_data_available`
  - `source_ticket_evidence_without_field_scenario`
  - `no_source_ticket_evidence`
- Excel, JSON, and HTML each expose the same catalog state.

Run the targeted test and observe it fail before implementation. Make the
smallest builder/catalog change that satisfies the contract, then rerun it.

### 3. Update the Builder, Not Individual Artifacts

Make changes at the owning abstraction:

- Service Function catalog and matching precedence.
- Scenario classification and source-evidence enrichment.
- Shared tree metadata.
- Excel headers/rows, JSON payload, and HTML renderer together.

Never hand-edit generated Excel, JSON, or HTML to patch a release. The next
rebuild would erase it and leave the three artifacts inconsistent.

### 4. Run Focused Regression Tests

Run the targeted test first. Then run the complete scenario-builder test file:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_phase09_idcevo_scenario_build.py -q
```

Do not generate final artifacts while this test file has failures caused by the
current change.

### 5. Generate the Triple from One Build

For a full V12 rebuild, use the builder APIs in a single Python process:

```python
from pathlib import Path

from backend.analytics.phase09_idcevo_scenario_build import (
    build_excel,
    build_families,
    build_knowledge_cards,
    build_risk_scores,
    build_scenarios,
    load_excel_metadata,
    load_extractions,
)

output_xlsx = Path(r"<output-stem>.xlsx")
output_html = output_xlsx.with_name(
    f"{output_xlsx.stem}_Decision_Tree_Catalog.html"
)
output_json = output_xlsx.with_name(
    f"{output_xlsx.stem}_Decision_Tree_Knowledge_Base.json"
)

extractions = load_extractions()
metadata = load_excel_metadata()
scenarios, excluded = build_scenarios(extractions, metadata)
families = build_families(scenarios)
risk_records = build_risk_scores(scenarios, len(extractions))
knowledge_cards = build_knowledge_cards(scenarios, families)
build_excel(
    scenarios,
    families,
    risk_records,
    knowledge_cards,
    excluded,
    extractions,
    metadata,
    output_xlsx=output_xlsx,
    decision_tree_html=output_html,
    knowledge_base_json=output_json,
)
```

For a locked original Excel target:

1. Generate all three files using a unique candidate suffix, for example
   `<stem>_Function_Map_Expanded`.
2. Validate the candidate triple completely.
3. Ask the user to close the original Excel file.
4. Copy the candidate Excel, JSON, and HTML together to the original stem.
5. Compare candidate and final hashes for all three files.

When an original target is not locked but already exists, ask whether to write
directly to that stem or generate a separately named candidate triple. Do not
replace a release merely because its output paths are known.

### 6. Validate the Release as a Triple

Do not rely on a generator exit code alone.

Validate the following from the actual output files:

- All three files exist and share the expected stem/version.
- For current-state artifacts, `source_evidence_audit` matches the exact approved
  scope, verified current count, explicitly unavailable IDs and per-ticket source
  timestamps. Unavailable rows do not count as freshly analysed LLM records.
- Model version/fingerprints and cited evidence IDs match the current input;
  uncited/conflicting claims remain unknown or review-required with warnings.
- Count populated Excel cells with `value is not None and str(value).strip()`;
  `str(None)` is not populated content. Validate creation-date native types and
  UTC values, not just headers or formatting.
- JSON schema is expected and contains all requested Service Function IDs.
- `sum(decision_trees[].scenario_count)` matches Excel
  `01_Scenario_Library` nonblank rows.
- `sum(decision_trees[].ticket_count)` matches the reported field-scenario
  Ticket total.
- Excel catalog sheets contain the requested metadata columns.
- HTML contains the expected selected-tree metadata and does not retain removed
  workflow labels.
- For any newly requested function, state one of:
  - `field_scenario_data_available`
  - `source_ticket_evidence_without_field_scenario`
  - `no_source_ticket_evidence`
- If a candidate replaced an original triple, calculate SHA-256 for candidate
  and final `.xlsx`, `.html`, and `.json` files; all corresponding hashes must
  match.

### 7. Visually Verify HTML

Open the generated local HTML in the browser.

Check at least:

- Requested Service Functions appear in the selector, including entries with
  zero scenarios.
- A high-volume function and a zero/source-only function both render correctly.
- Metadata cards, tables, labels, and diagrams do not clip, overlap, or require
  unintended horizontal page scrolling at desktop and mobile widths.
- The selected function displays its source-evidence state and catalog status.

If the HTML is visually correct but the JSON or Excel fails an invariant, the
release is not complete.

## Completion Report

Report only verified facts:

- Exact output paths.
- Scenario total and field-scenario Ticket total.
- Requested Function Map feature counts by evidence status.
- Tests run and their pass/fail count.
- Browser verification result.
- Whether a candidate release was used because the original workbook was locked.

## Ambiguities to Resolve Per Release

Ask before generation if any of these are not specified:

1. Should the run use a full raw-data rebuild or an approved snapshot/backup?
2. What is the exact output stem/version, and may the current release be
   overwritten?
3. Which requested Function Map names are independent Service Functions versus
   aliases, subfeatures, or cross-domain tags?
4. Should source-only features remain visible with zero field scenarios?
5. Is the artifact intended for after-sales navigation, evidence review, or a
   distinct approved diagnostic workflow?