# DTSV Work-Package Matrix Implementation Plan

> **For agentic workers:** Execute inline with verification checkpoints. No delegation, commits, source-workbook edits, upstream synchronization or Confluence writes are authorized.

**Goal:** Deliver one Excel matrix with every original work type as a column, concrete work packages as rows, and consistent coloring for shared packages.

**Architecture:** Read personnel headers M2:AE2 and manager headers M33:AI33 from the named 5WP workbook. Merge only whitespace/case-identical headings, retain every source-cell reference, and keep related but distinct responsibilities separate. Render a proposed many-to-many activity mapping with outputs, evidence, strategy references and booking boundaries on one visible sheet.

**Tech Stack:** Python, openpyxl, standard-library HTML/JSON parsers, pytest; read-only ATC Confluence page snapshots.

## Task 1: Lock The Matrix Contract

Files: backend/tests/test_free_test_effort_breakdown.py.

- [x] Add a full-header fixture with 19 personnel and 23 manager work types, plus bounded ticket evidence and strategy-page snapshots.
- [x] Assert 32 unique work columns, 42 preserved source-header references, one visible worksheet, nonempty decomposition for every work type, and at least one multi-column package.
- [x] Assert shared row cells have the same color, different shared packages have distinct colors, and conditional participation remains explicitly labeled.
- [x] Assert source bytes remain unchanged and existing output/source paths cannot be overwritten.

Run the focused contract before implementation:

```powershell
& .\.venv\Scripts\python.exe -m pytest backend/tests/test_free_test_effort_breakdown.py -k work_package_matrix -q
```

Expected initial result: the new module is missing. Expected implemented result: all matrix tests pass.

## Task 2: Implement The Owned Exporter

Created backend/analytics/work_package_matrix.py with the public contract `build_work_package_matrix(source_path, evidence_path, strategy_paths, output_path) -> dict`.

- [x] Implement exact work-header discovery, source hash protection and no-overwrite checks.
- [x] Define concrete requirement/design, readiness/flash, execution/exploration, defect, reporting/governance, coordination and tooling packages. Associate original source-header cells with concise, role-specific actions; preserve distinct outputs rather than force artificial deduplication.
- [x] Derive all numeric evidence from the supplied local snapshot, not from assumptions or the newer HK cohort. Clearly distinguish method signals, compound candidates and missing operational measurements.
- [x] Embed freshly fetched strategy version/status, relevant clauses and bounded source excerpts in cell comments. Mark proposed mappings and conditional applicability; do not imply approved RACI or measured hours.
- [x] Render one sheet with frozen labels, filters, wrapped text, per-package overlap colors, source references, outputs and no hours/FTE allocation formulas.

## Task 3: Generate And Verify

- [x] Generate exports/test_method_evidence/work_package_matrix_20261102/DTSV_All_Work_Package_Matrix.xlsx from the supplied 5WP workbook, the 20261028 discovery-value JSON and the three freshly fetched ATC page snapshots.
- [x] Reopen the exact output and verify full header coverage, package IDs, mapping/color invariants, evidence values, source hashes and workbook integrity.
- [x] Use Excel to inspect the rendered table or export a temporary preview; do not save Excel-normalized changes over the source workbook.
- [x] Run all related tests, diagnostics and scoped whitespace checks, then link the single workbook and explain overlap semantics.

```powershell
& .\.venv\Scripts\python.exe -m pytest backend/tests/test_free_test_effort_breakdown.py backend/tests/test_test_method_evidence_report.py -q
git diff --check -- backend/analytics/work_package_matrix.py backend/tests/test_free_test_effort_breakdown.py docs/superpowers/plans/2026-11-02-dtsv-work-package-matrix.md
```

Expected: all focused tests pass; source SHA256 unchanged; 32 work types and all 42 source references retained; no additional analysis tabs or invented measured times.

Verified result: 32 work types, 55 concrete packages, 204 mapped relations including 46 conditional relations, one visible sheet and 55 consistent package colors. All 30 related tests pass; diagnostics and scoped whitespace checks are clean. Two native Excel previews were inspected. The named source workbook SHA256 remains 70751c590341aa6abb021241aaca6fe8924439d322b57394654417fc8842b134. A review finding was resolved by rejecting missing strategy pages and unreviewed versions; the validated main/CW34/CW36 versions are 89/14/24.

