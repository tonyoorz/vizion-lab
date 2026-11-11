# Phase 09 Lineage And Release Contract

## Verified Producers

| Stage | Owning producer | Inputs | Outputs | Governance invariant |
| --- | --- | --- | --- | --- |
| Raw Ticket payload | `backend/analytics/phase09_idcevo_report.py` / `build_phase09_idcevo_report_payload` | Octane SQLite source selected by `resolve_report_db_path` | In-memory Ticket payload | DB must contain `octane_defects` and `octane_defect_history_events`. |
| LLM input | `backend/analytics/phase09_idcevo_llm_summary.py` | Full Ticket payload | v7 JSONL | `ticket_id`, `input_fingerprint`, `input_version`. |
| LLM summary | `scripts/summarizePhase09LlmInputs.mjs` | v7 JSONL | Summary JSONL + errors JSONL | Resume only on matching fingerprint. |
| FullLLM Excel | `backend/analytics/phase09_idcevo_excel_report.py` | Ticket payload + summary JSONL | Closure Risk Excel and evidence sheet | `_load_llm_summaries` rejects nonmatching fingerprint/version. |
| Scenario input | `exports/idcevo_phase09/llm/export_v2_scenario_inputs.py` | FullLLM `05_全部明细` + evidence workbook | V2 extraction JSONL | `phase09-scenario-extract-v2` fingerprint. |
| Scenario LLM | `scripts/extractPhase09Scenarios.mjs` | V2 input JSONL | V2 output/errors JSONL | Resume only on matching Ticket/fingerprint. |
| V12 triple | `backend/analytics/phase09_idcevo_scenario_build.py` / `build_excel` | FullLLM metadata + V2 extraction output | Excel + KB JSON + V12 HTML | Build all three in one process. |
| V1 catalog | `backend/analytics/phase09_all_function_after_sales_catalog.py` | Validated V12 Excel/KB + source-tree registry | V1 HTML + JSON | Source image assets stay external and traceable. |

## Canonical Source Resolution

`backend/analytics/config.py` resolves the full-picture source database in this
order:

1. `VIZION_FULL_PICTURE_SOURCE_DB_PATH`
2. `VIZION_DATABASE_ROOT\source\qgate_raw.db`
3. Repository default `database\source\qgate_raw.db`

`backend/database/octane_data.db` is an analytics DB default/candidate in other
contexts. Do not substitute it silently for a full raw-source rebuild; record
the resolved database path and required tables first.

## Current Snapshot Caveats

- The 20260908 `FullLLM_Complete` workbook has full summary data but is missing
  its referenced raw-evidence sheet. The matching FinalProject workbook retains
  evidence and RCA/cluster sheets. Treat joins between those files as governed
  operations requiring Ticket ID/count/key-field validation.
- The V12 copy under `exports/idcevo_phase09` can be a reduced/inconsistent
  snapshot for source-tree overlay work. When the release requires the validated
  full V12 backup, use the explicit Excel and KB pair approved under
  `C:\Users\q446328\Downloads\defectdecisiontree`.
- Ticket recovery text is historical evidence. It must never be published as an
  approved repair merely because it appears in V12 or V1.
- RCA text is a candidate unless separately verified by approved source evidence.

## Database Snapshot Evidence Contract

Database-first Shift/Tolerate analysis uses
`scripts/rebuild_shift_current_evidence.py --from-database --project <recorded project> --run-directory <new directory>`.
It selects candidates from local metadata/comments/history, captures an immutable
scoped SQLite and creates bounded fingerprinted model inputs. No previous Excel,
KB, exact-refresh state, repair manifest or online endpoint is required.

This is explicitly an offline/as-recorded evidence lane, not verified live
coverage. Original source fetch timestamps are preserved; snapshot creation does
not make tickets fresh. Empty or missing evidence may be unfetched, and local
candidate selection can miss tickets whose evidence is absent. Audits must
report that limit rather than equate local consistency with source completeness.

The native build checks the saved snapshot hash, rebuilt-versus-saved inputs,
exact selected scope and matching summary version/fingerprint/citations before
report writes. Source records are `database_snapshot`, with a separate snapshot
integrity/count gate and zero claimed `current_verified` tickets. Do not require
the live contract below to run a historical snapshot analysis, and do not relabel
snapshot rows as live-verified to bypass it.

See the [database-first skill](../../shift-tolerate-current-analysis/SKILL.md)
for the complete commands, generated scope/audit files, native outputs, filter
contract and persistent model-cache reuse. Upstream Octane ingestion is an
independent process, not a stage of the on-demand analysis skill.

## Current Source Evidence Contract

This stricter contract applies to online-current evidence claims and exact-ID
repair, not the explicitly labeled offline snapshot lane above.

Before calling a release current/live, freeze the approved ticket IDs and verify each
ticket, not the database-wide maximum timestamp:

- Resolve the source path in the configured order above; both repair CLIs use
  `get_full_picture_source_db_path()` and accept an explicit `--db-path`.
- Back up the exact local scope before the first write. Keep the prior artifact
  triple unchanged. Completed backups are atomic and verified before reuse.
- Fetch current metadata/description, the comments endpoint and complete audit
  history. Failed or incomplete calls must not become successful empty results.
- Require identical canonical `comments`, comment refresh cache and
  `raw_json.comments`; metadata-only updates must preserve existing comments.
- Require current per-ticket `octane_ticket_evidence_refresh_state`, matching
  last-modified values, full history counts and normalized row-content equality
  against the fetched history snapshot, raw evidence fingerprints, and a
  successful fetched snapshot. Default age limit is 24 hours; do not loosen it
  simply to force a gate to pass.
- Default team-based nightly refresh does not establish coverage for an
  arbitrary analysis cohort. Refresh its approved IDs directly when needed.
- A collection omission or direct 404 does not prove deletion or absence of
  evidence. Preserve local historical rows and report unavailable IDs separately.
  They must not count as current verified coverage.

For an approved Shift/Tolerate KB, use a new repair directory:

```powershell
$kb = "<approved merged Knowledge Base.json>"
$repair = "exports\idcevo_phase09\V4\<unique source-repair directory>"

.venv\Scripts\python.exe scripts\refresh_shift_tolerate_evidence.py `
  --knowledge-base $kb --output-directory $repair
```

Strict mode stops on missing metadata. Only after reviewing and recording the
unreturned IDs may the same scope use `--allow-unavailable` together with
`--expected-unavailable-ids <reviewed IDs>`; this preserves those rows and
excludes them from current model analysis, not from historical scope. The exact
exception allowlist is stored in `source_refresh_policy.json`, checked during
fetch/resume/rebuild, and cannot silently grow. A changed policy requires a new
reviewed repair directory.
Never substitute empty metadata/comments/history to satisfy coverage.

```powershell
.venv\Scripts\python.exe scripts\rebuild_shift_current_evidence.py `
  --knowledge-base $kb --repair-directory $repair

node scripts\summarizeOpenTicketPostponementInputs.mjs `
  --input "$repair\current_state_llm_inputs.jsonl" `
  --output "$repair\current_state_llm_summaries.jsonl" `
  --model deepseek-v4-pro --concurrency 12 --max-retries 4 --limit 6
```

Inspect a dated sample including fix reports, retest requests, retractions and
later recurrence. Then run without `--limit`. The inputs use
`shift-current-evidence-v2`, with fingerprints binding the complete source
snapshot plus the bounded input. The transport preserves bounded JSON with an
explicit 100,000-character per-message / 120,000-character total limit and skips
the unrelated dashboard system prompt; over-budget evidence is rejected.

The current-state output separates `historical_shift_status`,
`current_postponement_status`, `repair_status`, and `delivery_status`, plus
dated supplied evidence references. Unknown/unclear is valid. Uncited affirmative
claims are downgraded with warnings, invented IDs are rejected, and conflicting
lifecycle/outstanding-delivery claims remain review-required. Diagnostic codes
may be redacted from presentation only, with warnings; full evidence remains
unchanged in SQLite and the raw-evidence workbook.
Unavailable records keep prior plans in explicit historical fields and clear
current plans, references and fingerprints. Current plan/version summaries must
prefer the dated current evidence analysis over an older structured target;
retain the original structured fields separately for comparison.

```powershell
.venv\Scripts\python.exe scripts\rebuild_shift_current_evidence.py `
  --knowledge-base $kb --repair-directory $repair `
  --summaries "$repair\current_state_llm_summaries.jsonl"
```

The rebuild revalidates the source and exact input fingerprints before writing
`candidate_v12`. It does not overwrite the existing release. The native merged
writer rejects current records without a verified exact-scope source audit.
Legacy workbook-only builds with no source audit are historical snapshots, not
current-state releases. A model label `verified` is evidence assessment, not
technical approval; `verified_repair_count` must not be inferred from that label.

Validate actual FullLLM/Excel/JSON/HTML values, source coverage, unique ticket IDs,
native UTC date cells, and clustered browser navigation before publication.
Keep business filters in `01_Scenario_Library`; retain a compact traceability
mapping sheet and no per-ticket navigation control. Obtain publication approval
before replacing all three official members, then compare SHA-256 hashes.

## Minimum Artifact Invariants

### FullLLM Excel

- Detail sheet has unique Ticket IDs.
- Summary source distribution is recorded; unmatched LLM inputs remain explicit
  rule-based placeholders rather than silently appearing as LLM output.
- Evidence sheet presence/absence is recorded with the release.

### V12 Triple

- Excel `01_Scenario_Library` row count equals KB sum of tree scenario counts.
- Excel ticket mapping count is compatible with tree ticket totals.
- `Service Function` values in Excel resolve to V12 KB Service Function IDs.
- Excel, KB JSON, and catalog HTML use one common output stem.

### V1 Catalog

- All V12 Service Functions are present.
- Every registered source asset path exists and is nonempty.
- Source graph topology has valid root, parent references, and edge endpoints.
- Pattern-box labels may be compacted for display, but full summaries remain in
  JSON/table/detail views.

## Windows Notes

- Use `.venv\Scripts\python.exe` rather than relying on `python` from PATH.
- Set `PYTHONIOENCODING=utf-8` for scripts that print or write Chinese text.
- The provided end-to-end scripts are Bash (`.sh`). On native Windows, use Git
  Bash/WSL or invoke the underlying Python/Node stages explicitly.
- Do not route secrets through chat or script arguments. Let `loadLocalEnv` and
  the local environment provide configured credentials.
