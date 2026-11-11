# Octane CN Speech Evidence and Automation Plan

**Goal:** Read all current tests covering Epic 1685240 and its descendants, preserve their manual scripts, and derive traceable AIGENT-compatible automation drafts.

**Architecture:** Reuse the authenticated Octane client for GET requests only. Keep the evidence snapshot and generated bundle local. Separate the native Python/YAML execution shape from a non-executable scenario catalog and skill. No vehicle operations, Octane writes, Jira uploads, package installation, commits, or changes to the AIGENT checkout.

**Tech Stack:** Existing Python environment, requests-based Octane client, pytest, standard-library JSON/CSV/AST, installed YAML parser if available.

## Task 1: Complete Read-Only Evidence Snapshot

- [x] Add and run `backend/tests/test_octane_epic_automation.py` before implementing the exporter.
- [x] Implement `collect_epic_evidence(client, epic_id, batch_size, workers)` in `backend/analytics/octane_epic_automation.py`. Traverse `parent`, retrieve `tests.covered_content`, deduplicate test IDs, preserve raw metadata, and GET each manual test's `/script` resource including version fields. Report any script failure as incomplete.
- [x] Run `.venv/Scripts/python.exe -m pytest backend/tests/test_octane_epic_automation.py -q`.
- [x] Run the exporter against Epic 1685240, using a local resumable script cache and a manifest with counts, timestamps, API scope and hashes. Verify no script errors before calling the snapshot complete.

## Task 2: Evidence-Grounded Automation Bundle

- [x] Inspect feature coverage and actual action/expected-result scripts, not titles alone. Preserve every test ID in the scenario mapping, with explicit unmapped/ambiguous states.
- [x] Create a local JSON/CSV inventory, an English report with implementation priorities, an AIGENT-native `tests: [path]` YAML plan, and a separate scenario-specification YAML. Do not invent executable YAML action syntax.
- [x] Generate a native Python entry point that returns inconclusive before importing framework/device code until reviewed audio input and domain-result adapters exist. Never report a deferred check as PASS.
- [x] Create a draft skill describing the evidence-to-test workflow, Chinese input/OCR prerequisites, result oracles, safety gates and traceability. Keep it outside active skill discovery until reviewed.
- [x] Validate JSON/YAML cross-references, all-test mapping, Python AST/import safety and deferred exit code. Document explicitly that device execution and Speech correctness are not validated.

## Verified Outcome

- Output: `exports/octane_speech_epic_1685240/20261028/ANALYSIS.md` plus ten supporting evidence/draft files.
- 267 manual scripts, one separate suite, 57/59 Features covered, 16 active candidate families and five proposed MVP cases. No missing scripts, unresolved coverage or unmapped manual cases.
- All source script text/version evidence retained. All current Feature counts reconcile with Octane; JSON/YAML/CSV and snapshot SHA-256 verified.
- Twenty-four focused offline checks pass, including invalid-cache fallback, complete root metadata, missing-oracle rejection and restoration before summary.
- Whole-set structural parsing and representative semantic review completed; per-case certified semantic design and device/hardware execution are explicitly not claimed.

## Observed Contracts

- Epic 1685240 is `IDCEVO_Speech_CN`, with 59 direct child Features and zero tests directly covering the Epic.
- AIGENT native tests use `run_test(device_ip=None, test_execution_key=None)`, `TestExecutor.execute_step(...)`, and `print_summary()`, not an invented BaseTest class.
- Native local-plan YAML contains a nonempty `tests` list of relative Python paths; unknown `enabled` or `skip` keys do not disable execution.
- Native exit code 2 is inconclusive/skip, not a passed Speech test. The local plan can exit 0 when all entries skip, so its aggregate status is not evidence of automation readiness.
- Existing inspected IPA examples demonstrate UI/PPS workflows, not a verified Chinese Speech audio-injection/ASR/intent adapter.

## Task 3: Defect-Grounded Excel Design Library

**Goal:** Add the reviewable specification layer before script generation. Preserve all 267 baseline cases and distinguish explicit defect relations, text-similarity suggestions and unapproved regression candidates.

**Files:** `backend/analytics/cn_speech_test_design.py`, existing `backend/tests/test_octane_epic_automation.py`, and a separate `design_library` export subdirectory.

- [x] Add failing tests for actual-versus-expected separation, exact scenario deduplication, explicit relation provenance and insufficient-evidence retention.
- [x] Implement the pure design model; run the focused test file. Existing tests remain intact.
- [x] Read local SQLite in read-only mode. Resolve run/cache links for the 267 source IDs, plus clearly labelled IDCEVO CN Speech topic candidates. Retain the local acquisition dates; no upstream synchronization.
- [x] Preserve every numbered action, source version, defect description and expected-result authority. Do not treat source links or similarity as confirmed failure-mode coverage.
- [x] Generate a canonical JSON library and Excel sheets for cases, steps, automation design, traceability, defect review, raw evidence and pending Octane publication content. No production IDs, acceptance thresholds, approvals or readiness are invented.
- [x] Validate workbook/JSON counts and cross-references, script provenance, cell/formula safety, field lengths and default blocked status. Read-only SQLite use and unchanged fixture contents are tested; no claim is made about concurrent external database writers. Document that Excel import compatibility and real device execution are not established.

Focused command: `.venv/Scripts/python.exe -m pytest backend/tests/test_octane_epic_automation.py -q`.

Verified Task 3 output: `design_library/CN_Speech_Automation_Test_Design.xlsx`, canonical design JSON, frozen defect evidence, manifest and review guide. 267 baseline cases plus 102 candidates derived from 104 explicit procedures; 369 cases, 780 valid action rows, 708 traceability rows. Selected 283 defect records include 250 available bodies and 33 linked missing bodies. 24 mixed procedures stay pending; 33 baseline tests marked Outdated remain visible. All 41 focused checks pass. No Octane writes, upstream synchronization or device operations; no approved/ready cases claimed.
