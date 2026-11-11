# Shift Database Bootstrap Workflow Implementation Plan

> Execute inline with focused checks. Do not create branches, commit, run a paid model batch or overwrite current reports.

**Goal:** Generate the existing Shift/Tolerate FullLLM and filter-enabled V12 outputs starting from a scoped SQLite database, without requiring previous Excel, KB or repair artifacts.

**Architecture:** Add an immutable database-snapshot preparation path and reuse existing candidate classification, bounded current-evidence inputs, resumable model runner, summary gates and native artifact writers. Keep offline snapshot evidence distinct from online-refreshed evidence. Retain the approved-KB repair path as a separate compatibility mode.

**Tech Stack:** Python, SQLite, existing Node model runner, openpyxl, existing standalone HTML renderer.

## Tasks

### 1. Database-Only Scope And Snapshot

- [x] Add `backend/analytics/shift_database_workflow.py` with `prepare_database_workflow(database_path, run_directory, project, created_from)`.
- [x] Select explicit project/date scope from canonical ticket rows; reuse process and Open Shift candidate predicates. Do not assume a fixed 1,805-ticket count or use old artifact IDs.
- [x] Persist selected IDs, an immutable scoped SQLite snapshot, a selection/source audit and model inputs. Snapshot copying uses a single read transaction; the original source remains read-only.
- [x] Test from a temporary database with open/process/noncandidate/other-project records and no old workbooks, KB or refresh state. Verify source bytes unchanged and run-scope mismatch rejected.

Focused command: `.venv\Scripts\python.exe -m pytest backend/tests/test_shift_database_workflow.py -q`.

### 2. Snapshot-Aware Evidence And Native Build

- [x] Reuse `build_current_evidence_input`, `build_shift_full_records` and `apply_current_evidence` to construct records without baseline scenarios.
- [x] Preserve snapshot origin, actual source timestamps and uncertain fetch completeness. A newly copied snapshot is not a newly fetched ticket.
- [x] Bind inputs and build-time verification to the immutable snapshot. Reject edited snapshots, missing/stale summaries and unsupported citations before writing reports.
- [x] Extend the native writer audit contract to distinguish snapshot records from live-refresh records. Keep current-source strict gates unchanged.
- [x] Test the full native source/triple from fixture summaries, native dates, HTML filter defaults and no false live-source claims.

Focused commands:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_shift_database_workflow.py backend/tests/test_shift_tolerate_merged_v12.py -q
npm test -- src/test/server/openTicketPostponementLlmRunner.test.ts
```

### 3. Existing CLI Database Mode

- [x] Extend `scripts/rebuild_shift_current_evidence.py` with mutually exclusive database/approved-KB inputs and a run-directory alias.
- [x] Preparation requires explicit project scope and creates JSONL/snapshot/audit outputs without an Octane client or model call.
- [x] Building reuses the saved snapshot and complete matching summaries, preserving alternate candidate output routing.
- [x] Test CLI prepare/build from an empty run directory without legacy files and verify the old approved-KB interface remains available.

### 4. Complete Skill And Phase 09 Comparison

- [x] Update `.github/skills/shift-tolerate-current-analysis/SKILL.md` so database-only generation is the primary workflow and network repair is optional/separate.
- [x] Compare stages with `.claude/skills/phase09-llm-closure-audit/SKILL.md`: shared infrastructure versus distinct scope, prompt/schema, input versions and outputs. Link rather than duplicate the Phase 09 implementation.
- [x] Document exact commands, full output inventory, snapshot caveats, resume behavior, filter defaults and publication checks.
- [x] Correct parent-skill routing and source-contract wording so historical snapshot analysis does not require an online repair manifest.
- [x] Validate skill YAML, local links, PowerShell syntax, real CLI help and focused regressions. No full production model batch is required for this implementation.

## Acceptance

- Only a readable canonical database and explicit business scope are needed to prepare a new run.
- No old Excel/KB/repair directory is read in database mode.
- Offline provenance is explicit and never reported as verified current Octane coverage.
- Snapshot/content and input/schema/citation gates run before native output generation.
- Fixture end-to-end generation produces FullLLM plus matching Excel/JSON/HTML with phase/date filters and unchanged source data.
- Existing approved-KB source repair and existing report paths remain intact.

## Verified Results

- 84 focused Python tests and 31 Node tests passed. Fixture tests generate all four native outputs from a fresh temporary source/run directory with synthetic test summaries and no model request.
- Snapshot edits and stale/missing summary coverage fail before output writes. Project-scope mismatch is rejected. Fixture source bytes remain unchanged.
- Actual database-only preparation scanned 1,435 IDCEVO tickets created since 2026-10-01 and selected 76 candidates into `exports/idcevo_phase09/V4/database_bootstrap_smoke_20261020_145947`. It generated the scope, immutable SQLite, audit and model inputs without online collection or model calls.
- YAML/local-link checks passed for the updated skills/contracts; all 10 PowerShell examples parsed. Editor diagnostics were clean and read-only review found no actionable issue in this slice.
- No full production model batch was run. No existing Excel, JSON or HTML report was overwritten. Offline snapshot evidence remains explicitly distinct from live-source verification.
