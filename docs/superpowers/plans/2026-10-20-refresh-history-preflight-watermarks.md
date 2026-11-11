# Refresh Preflight And Watermarks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject unavailable history access before expensive refresh work and replace repeated wide-table watermark scans with one indexed query.

**Architecture:** Add a bounded history-access check to the existing Octane client and run it before source-store initialization when history is required. Batch watermark reads across selected teams, preserving the existing normalized team/year expressions and three-day overlap. Add a covering expression index through the existing source-store schema setup.

**Tech Stack:** Python, requests, SQLite, pytest, Windows PowerShell.

---

## Task 1: History Permission Preflight

**Files:**
- Modify: `backend/analytics/ingest/client.py`
- Modify: `backend/analytics/ingest/pipeline.py`
- Test: `backend/tests/test_octane_ingest_pipeline.py`

- [x] Add tests requiring `check_history_access()` before source DB creation and all defect fetches. A 403 must propagate; explicitly disabled history must not call this check.
- [x] Run `python -m pytest backend/tests/test_octane_ingest_pipeline.py -k history_access -q` and confirm the new behavior fails first.
- [x] Add `OctaneApiClient.check_history_access()`. Request `history_logs` with `limit=1`, `offset=0`, a 30-second timeout, and the existing history-query builder for defect ID `0`, avoiding retrieval of actual ticket evidence. Share history-page transport with normal pagination and require a JSON `data` list.
- [x] Invoke the check before `OctaneSourceStore` construction when `include_history` is true, with start/success timing logs. Never catch a 403 and convert it to empty history.
- [x] Rerun the focused tests, then the whole ingest-pipeline test file. Result: 30 passed.

## Task 2: Indexed Batched Watermarks

**Files:**
- Modify: `backend/analytics/ingest/pipeline.py`
- Modify: `backend/analytics/ingest/source_store.py`
- Test: `backend/tests/test_octane_ingest_pipeline.py`

- [x] Add fixture rows covering multiple teams, missing years, blank finder-team fallback, whitespace, missing `last_modified`, unrelated teams, and three-day overlap.
- [x] Assert the multi-team watermark loader executes one grouped source SELECT and returns the same per-team/year values as the old loader.
- [x] Run the focused watermark tests and confirm the new batched entrypoint is missing.
- [x] Add the batch loader and route refresh target planning through one call. Keep the single-team helper as a compatible wrapper and preserve forced-full refresh behavior.
- [x] Add the covering index using the exact existing expressions: normalized finder-team with team fallback, integer year, and modified/fetched watermark. Do not rewrite business data or change scope semantics.
- [x] Assert `EXPLAIN QUERY PLAN` uses the new index; rerun watermark and full ingest/source-store regression tests. Ingest suite: 32 passed.

## Verification And Operating Boundaries

- [x] Run affected ingest, source-store, CLI, and nightly-script tests; check diagnostics and focused `git diff --check`. Combined suite: 79 passed, with two existing SWIG deprecation warnings; diagnostics and patch checks clean.
- [x] Run the normal refresh script once. If `history_logs` still returns 403, verify early failure and unchanged source data; do not skip history or repeatedly trigger authentication.
- [x] Where safe, create only the additive index on the local source and measure the batched watermark read with its query plan. Clearly distinguish query timing from a complete successful refresh.
- [x] Record actual timings and the unresolved upstream permission requirement. Do not commit, create branches, modify credentials, or change Octane permissions.

## Measured Results

The normal refresh attempt is recorded in `database/hot/logs/nightly-source-refresh-20261020-173500.log`.

| Check | Result |
| --- | --- |
| History preflight | 403 rejected in 0.7 seconds |
| Normal wrapper elapsed time | 2.476 seconds, exit code 1 |
| Source file at preflight failure | Size and modified time unchanged |
| Authentication recovery | Not triggered for the 403 |
| One-time additive index installation | 48.583 seconds |
| Batched watermark read | 2.871981 seconds for 7 teams and 14 team/year slots |
| Query plan | SEARCH using idx_octane_defects_incremental_watermark |
| Defect rows before/after index creation | 214236, unchanged |
| Duplicate-search revision | Unchanged by the index installation |

These initial measurements verified fast failure and the indexed watermark query, not a successful complete refresh. No history was skipped or replaced by synthetic empty evidence. The 403 was subsequently resolved by restoring the history client context, as recorded below.

## Follow-up: History 403 Root Cause Corrected

The earlier 403 did not establish that the user lacked a history role. A fresh mobile-push session was tested against the same ticket and query without changing account or authentication tokens:

| History request context | Result |
| --- | --- |
| No client-type marker | 403, resource access denied |
| `HPECLIENTTYPE: HPE_MQM_UI` request header | 200, real history returned |
| Existing browser-style client-type cookie | 200, real history returned |

The old browser cookies included this non-secret marker; mobile-push session export did not. `OctaneApiClient._fetch_history_page()` now sets the client-type header for preflight, single-ticket, batch and paginated history reads. It does not change identities, roles, tokens, evidence scope or response interpretation.

Validation: the original failing five-ticket batch returned all five tickets and 68 history entries. The affected ingest, source-store, CLI and nightly tests passed (79 tests). The production rerun `database/hot/logs/nightly-source-refresh-20261020-174903.log` completed successfully at 17:59:09 with exit code 0 after 606 seconds. It processed 2772 defects, wrote 306860 structured history rows in the refresh windows, and processed 1347 manual runs. These are processed/written counts, not net-new record counts. Processor duration was 5.415 seconds. Outcomes refreshed 2772 affected rows incrementally in 362.3 seconds; the resulting outcomes store contained 113942 rows. Duplicate-index warmup was not enabled.