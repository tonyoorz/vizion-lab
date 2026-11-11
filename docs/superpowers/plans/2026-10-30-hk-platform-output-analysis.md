# HK Platform Output Analysis Implementation Plan

> **For agentic workers:** Execute inline with focused test checkpoints; no source synchronization, source updates, commits, or worktree changes.

**Goal:** Report 2025-2026 HK VIN-attributed execution, testcase/AIDA coverage, and defect output, explicitly including HY52877.

**Architecture:** Read the existing SQLite database in read-only mode. Match full VINs or bounded known seven-character VIN suffixes from execution vehicle fields and defect VIN fields, never infer an execution vehicle from its defect. Export auditable rows plus aggregates, with source dates and attribution limitations.

**Tech Stack:** Python sqlite3, pytest, openpyxl, static HTML/JavaScript.

---

## Task 1: Lock Attribution and Time Rules

**Files:**

- Create: `backend/tests/test_hk_platform_output_report.py`
- Create: `backend/analytics/hk_platform_output_report.py`

- [x] Add synthetic fixtures with passed/failed/planned runs, raw VIN fallback, multi-VIN defects, a linked defect on another vehicle, and historical execution dates.
- [x] Run `./.venv/Scripts/python.exe -m pytest backend/tests/test_hk_platform_output_report.py -q`; confirm the missing report implementation is the failure.
- [x] Implement `match_vins(value, known_vins)` and `build_report(db_path)`.
- [x] Rerun the same tests until VIN matching, date filtering, deduplication, and AIDA code grouping pass.

## Task 2: Generate the Local Report

**Files:**

- Update: `backend/analytics/hk_platform_output_report.py`
- Generate: `exports/hk_platform_analysis/20261030/`

- [x] Include the six screenshot-confirmed VINs and separately labelled historical candidates from the existing HK mapping; exclude ambiguous HK-DE classification from the confirmed inventory.
- [x] Use Asia/Hong_Kong calendar dates: run finished, then started, then labelled creation-date fallback; defects use creation time. Keep planned records separate from executed statuses.
- [x] Export source-scoped quality counts, per-year/month/VIN/AIDA aggregates, unique testcases, defect details, unresolved VIN attribution, source freshness, and clear non-productivity/non-causal boundaries.
- [x] Render Excel, static HTML, JSON and detailed CSV exports without changing source records or the existing application.

## Task 3: Verify Deliverables

- [x] Run focused tests and check editor diagnostics for the two new Python files.
- [x] Generate against the real read-only source; reconcile run/defect IDs and cross-foot aggregates independently.
- [x] Reopen Excel/JSON/CSV outputs and validate their row counts, formula safety, local links, and scope.
- [x] Verify desktop/mobile static HTML using available browser tooling or installed Playwright, including nonempty charts, filtering, and no horizontal page overflow.
- [x] Confirm source size/mtime and logical data are unchanged; report the actual source cutoff and all attribution gaps.

## Verified Starting Facts

- Source: `database/source/qgate_raw.db`, opened with `mode=ro` and `query_only=ON`.
- Local executions: 129,852; nonempty `testplatformid`: 62,952; sample values are full VINs and raw key is `testplatformid_udf`.
- Defects contain direct `vin`; use that rather than the stale market mapping for inclusion.
- The release/ingest `year=2025` contains finished timestamps from 2020, so its label is not a calendar execution filter.
- HY52877 is HK by explicit user confirmation. Existing user worktree edits are unrelated and must remain untouched.

## Initial Delivery Evidence

- 12 focused pytest checks pass. Browser checks pass for year, VIN, inventory evidence, AIDA search, pagination, source-quality scope, person-vs-method labels, nonempty charts, and desktop/mobile overflow; no JavaScript errors.
- Final known-inventory defects: 911 unique IDs (2025: 537; 2026: 374); confirmed six: 593 (473; 120); legacy candidates: 318. HY52877 has 6.
- Current VIN fields alone gave 398 tickets. The latest non-cleared VIN history recovered 513 more, including missing VINs with stale non-HK market labels. Current nonempty values always win; explicit later clearing is not undone. SQL window-function reconciliation independently matched all 911 IDs.
- Final function/AIDA entries: 118; directly attributable execution records and distinct testcases: 12, all in January 2025. Passed: 10; Requires Attention: 2; 3 AIDA codes with executions.
- Local calendar-2026 executions: 27,236, all without a vehicle identifier. These are not zero HK executions; HK assignment is unknown. A total of 352 run records linked to HK defects remain excluded because their own vehicle attribution is not HK-confirmed.
- Existing Octane cookie returned 401 with system-CA TLS verification; the existing keeper had no sessions. No new login or synchronization was triggered. Supplemental full execution coverage remains data/authentication-limited and is disclosed in the artifacts.
- Source fetch cutoff is 2026-10-20. Source file size and nanosecond mtime stayed unchanged; no source writes, commits or application changes occurred.
- Deliverables: `exports/hk_platform_analysis/20261030/`, including 13-sheet Excel, static HTML, frozen JSON, Markdown analysis, 9 audit/summary CSVs and desktop/mobile screenshots.

## Follow-up: Complete HK-Marked Testcase Analysis

- [x] Add an independent, explicitly title-marked HK testcase cohort; deduplicate catalog IDs while retaining title variants and classification provenance.
- [x] Include every catalog candidate, no-period-record cases, all matching 2025-2026 execution records regardless of VIN, latest results, annual/monthly/AIDA summaries, and overlap with VIN-attributed records.
- [x] Integrate cohort selection and case status/coverage views into the existing HTML; extend the same workbook, frozen JSON, Markdown and CSV exports.
- [x] Validate focused tests, independent source counts, workbook/CSV/JSON parity, desktop/mobile browser controls, and source preservation.
- [x] Keep region intent distinct from physical execution attribution; local testcase rows are synthesized from runs, not a complete native Octane testcase/script inventory.

### Follow-up Verification Results

- 19 focused tests pass, including source classification, Planned separation, latest results, cohort overlap, export parity, exactly-once aggregation, zero-VIN-defect databases and unassessed defect metrics.
- HK-marked cohort: 375 IDs = 356 catalog-title IDs + 19 run-title-only IDs. All title variants and classification sources are retained.
- Period records: 2,617 = 2,596 executed + 21 Planned; 260 distinct executed cases. 115 IDs have no local execution record in the selected years.
- Calendar 2025: 1,241 executed, 204 cases, 931 Passed / 175 Failed / 135 Requires Attention. Calendar 2026: 1,355 executed, 237 cases, 1,254 Passed / 44 Failed / 57 Requires Attention, plus 21 Planned.
- Latest observed executed case outcomes: 231 Passed, 9 Failed, 20 Requires Attention; 115 without local period execution. These are across-platform observed outcomes, not release-specific certification.
- AIDA execution summary: 23 observed function entries. Unique linked defect IDs: 46, of which 36 are also in the VIN defect cohort; association is not initial discovery. Unassessed title-cohort VIN defect/phase metrics are null, not zero.
- VIN/title overlap: 10 executed run IDs. Original VIN evidence stays 12 executions and 911 unique defects; the cohort totals are not additive.
- Independent source-ID/date/status/latest-outcome reconciliation passed. Excel now has 20 sheets and six additional HK-case CSVs; JSON and Markdown include the same analysis.
- Browser checks passed for cohort switching, annual/monthly/AIDA/results views, year, latest-failure/attention/no-execution filtering, case search, pagination, VIN defects, VIN latest-result completeness, and nonempty charts. Desktop/mobile have no page overflow or JavaScript errors.
- A review found and tests fixed a repeated-analysis placement error, VIN Results catalog leakage and misleading catalog/defect labels. End-to-end generation now succeeds with exactly one testcase analysis pass.
- Source file size and nanosecond mtime remain unchanged. No upstream synchronization, new login, or source update was performed.

## Follow-up: Bench / Vehicle Evidence Comparison

- [x] Profile the local execution environment fields before classifying anything. HK-title records contain 4 dWKP-only tags, 12 dual dWKP+sWKP tags and 2,601 without platform taxonomy; 16 have Manual Runner metadata.
- [x] Keep platform taxonomy groups mutually exclusive and preserve raw values. Neither a missing VIN nor an existing VIN, model, Manual Runner or ECU configuration establishes actual bench/vehicle execution.
- [x] Mark actual bench executions, actual vehicle executions and same-case/software comparison unassessed (null), with actual comparison readiness false. No physical classification is inferred while taxonomy semantics remain unverified.
- [x] Integrate an Environment view with observed tags, AIDA/tag matrix, per-run evidence and seven conditional capability dimensions; apply the current year, testcase-outcome and VIN cohort filters to evidence views.
- [x] Extend the existing workbook to 25 sheets, add five environment/capability CSVs, and document missing evidence and limits in Markdown/JSON.
- [x] Run 22 focused tests, independent source-field and artifact reconciliation, browser controls and desktop/mobile overflow checks. All passed, with no JavaScript or editor diagnostic errors.

### Environment Evidence Boundary

Actual platform comparison is data-limited, not a measured zero-versus-zero result. Complete physical-environment comparisons require an execution-ID-to-actual-platform mapping with provenance and event-time validity. Matched result comparisons additionally require testcase revision, software, release and relevant device/region configuration; effort comparisons need measured active/wait/setup time. Existing HK testcase counts, VIN counts and defects remain unchanged. No upstream login, synchronization or source updates were performed.
