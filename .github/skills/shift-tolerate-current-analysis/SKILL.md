---
name: shift-tolerate-current-analysis
description: "Generate, explain, audit or publish combined Open Shift/Phase 09 Shift/Tolerate artifacts on demand from the continuously updated local SQLite database. Upstream Octane synchronization is separate and must not run in this workflow. Freeze a new local snapshot, reuse per-ticket/model/version/evidence-matched LLM summaries, analyse only new or changed evidence through the configured Beacon/local proxy, and regenerate FullLLM plus filter-enabled V12 Excel/JSON/HTML. Use for incremental combined-report updates, postponed-ticket summaries, ticket/RCA analysis, phase/date filters, token-cost or model-routing questions, and exact-path publication."
argument-hint: "[update from local DB | reuse analysis cache | explain model routing | compare Phase 09 | publish exact path]"
user-invocable: true
---

# Shift/Tolerate Current Analysis

Operate the existing repository workflow. Do not create a parallel analysis
implementation, manually patch generated workbooks, or run the pipeline merely
because the user asks where its code lives.

Octane synchronization is maintained by an independent upstream job. This skill
reads the local database as it stands when invoked. Do not call Octane endpoints,
run synchronization/repair scripts, modify the source DB, or configure scheduling
as part of this workflow. Report source-data gaps to the upstream owner instead.

## Scope And Routing

- This skill owns the merged Open Shift plus historical Phase 09 Shift/Tolerate
  cohort and its current-state evidence trees.
- A new artifact run starts with the current local database and explicit project/date scope. It must
  not require an old KB, Excel, Octane refresh manifest or fetched snapshot.
  Old outputs may seed a valid analysis cache, but they are not scope inputs.
- New run does not mean new model calls for every ticket. Always reuse a stable,
  model-specific summary JSONL across runs, and let the validation gate decide
  which records remain reusable.
- Database selection reuses Open Shift and historical process predicates, not a
  fixed old ID list. New scope/data can change counts; reproducing the current
  effect means the same analysis/output/filter behavior, not forcing 1,805 IDs.
- A local snapshot is evidence as recorded, not proof of current Octane coverage.
  Missing comments/history or fetch provenance remain explicit uncertainty.
- This is not the 18,000-ticket Phase 09 closure/scenario-extraction pipeline,
  the Phase 06 RCA lane, QGate KPI reporting, or cross-project recurrence analysis.
  Use the [parent workflow](../phase09-ticket-to-aftersales-decision-tree/SKILL.md)
  for those separate Phase 09/06 routes.
- Read the [database snapshot contract](../phase09-ticket-to-aftersales-decision-tree/references/lineage-and-release-contract.md#database-snapshot-evidence-contract)
  for database-first mode. Apply the
  [artifact triple contract](../phase09-analysis-artifact-triple/SKILL.md) when
  generating or publishing the Excel/JSON/HTML set.

## Code Map

| Stage | Owning code | Responsibility |
| --- | --- | --- |
| Database-first preparation/build | [shift_database_workflow.py](../../../backend/analytics/shift_database_workflow.py) | Explicit source scope, existing candidate rules, immutable scoped SQLite, audit/inputs, complete matching summaries and native output. |
| Open Shift candidate rules | [open_ticket_postponement.py](../../../backend/analytics/open_ticket_postponement.py) | Ticket/history-derived facts and existing Open Shift candidate predicates, not a frozen old ID list. |
| Historical candidate classification | [phase09_wide_postponement_scan.py](../../../backend/analytics/phase09_wide_postponement_scan.py) | Process/version candidate mining; lexical matches require review. |
| Evidence gates and current records | [shift_current_evidence.py](../../../backend/analytics/shift_current_evidence.py) | Freshness/content checks, dated inputs, source-bound fingerprints and conservative current-state enrichment. |
| Prepare/build entry point | [rebuild_shift_current_evidence.py](../../../scripts/rebuild_shift_current_evidence.py) | `--from-database --project ... --run-directory ...`; snapshot/summary validation and native candidate output routing. Legacy repair modes are outside this skill. |
| Incremental model batch | [summarizeOpenTicketPostponementInputs.mjs](../../../scripts/summarizeOpenTicketPostponementInputs.mjs) | Persistent summary JSONL, ticket/model/version/evidence matching, bounded concurrency and retries. |
| Model schema and validation | [openTicketPostponementLlmRunner.mjs](../../../server/openTicketPostponementLlmRunner.mjs) | `shift-current-evidence-v2`, independent status axes, citations and unsupported-claim guards. |
| Model transport | [companyChat.mjs](../../../server/companyChat.mjs) | Explicit bounded analysis-message budget; model routing uses existing local configuration. |
| Scenario/tree and artifact generation | [open_shift_scenario_triple.py](../../../backend/analytics/open_shift_scenario_triple.py) | `build_merged_shift_tolerate_artifact_triple`, clustered nodes, FullLLM and V12 writers, filter payload. |
| Browser rendering and filtering | [phase09_all_function_after_sales_catalog.py](../../../backend/analytics/phase09_all_function_after_sales_catalog.py) | Shared static HTML renderer; phase/date filtering, pruning, counts, details and collapsed evidence. |

```text
Canonical SQLite + explicit project/date scope
  -> recorded Open Shift/process candidate selection
  -> immutable scoped SQLite + scope/source audit
  -> dated bounded inputs + semantic evidence fingerprint
  -> reuse valid summaries; model calls only for cache misses
  -> complete cited-analysis JSONL
  -> validated current records + raw local evidence
  -> FullLLM source + synchronized V12 Excel/KB/HTML candidate
  -> browser-only phase/date filters
  -> verified publication to the explicitly requested path
```

The upstream synchronization job is outside this graph. A fresh run reads the
new local state; resuming an old run intentionally keeps its old frozen snapshot.

## Choose A Run Mode

1. **Explain/audit:** inspect code, manifests and artifacts read-only. No Octane,
   model calls or artifact writes are needed.
2. **Update from local DB, default:** select project/date scope again, prepare a
   new frozen snapshot, compare against the persistent analysis cache, run only
   missing/invalidated model work, and regenerate all native files. It never
   updates source or initiates synchronization.
3. **Resume a run:** verify the frozen snapshot and matching cache. This resumes
   that run, not the newest local database. Use a new run to pick up DB updates.
4. **HTML/filter change:** change the owning renderer/payload and run focused
   tests. Keep the full data and distinguish the filtered view from a new analysis.
   Reuse validated summaries; do not call the LLM for a display-only change.
5. **Publish:** validate the candidate first, then update the exact user-approved
   destination. A new directory is not an update to the user's existing file.

## Preflight

Record the mode, project/date scope, source DB, new run/output directory,
persistent model-specific cache path, selected model, exact publication path and
overwrite permission.
Ask only for missing controlling inputs. Keep credentials out of chat, commands,
skill files and reports; use the existing local configuration.

Source DB resolution is explicit `--db-path`, then
`VIZION_FULL_PICTURE_SOURCE_DB_PATH`, then
`VIZION_DATABASE_ROOT/source/qgate_raw.db`, otherwise repository
`database/source/qgate_raw.db`. The analytics DB is not the raw-source default.

For a new database scope, obtain the project/date selection, not an existing KB.
Project matching uses the recorded nonblank `tproject`, falling back to `project`;
it is an exact case-insensitive label match. Do not infer a project from software
strings or silently expand a frozen old scope to the entire database. CLI dates
define the persisted source population; HTML dates only filter its view.

## Database-First Workflow

This is the primary zero-artifact route, analogous to the Phase 09 pipeline's
DB payload -> bounded JSONL -> resumable model -> native report stages.

### 1. Read, Select, Freeze And Prepare

Run from the repository root; select the actual business scope rather than
treating the sample project/date as a universal rule.

```powershell
$run = Join-Path 'exports\idcevo_phase09\V4' ('database_analysis_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
$output = Join-Path $run 'candidate_v12'

& .\.venv\Scripts\python.exe scripts\rebuild_shift_current_evidence.py `
  --from-database --project IDCEVO --created-from 2025-01-01 `
  --db-path 'database\source\qgate_raw.db' --run-directory $run
if ($LASTEXITCODE -ne 0) { throw 'Database preparation failed; do not run the model.' }
```

The source is read-only. Selection uses the existing Open Shift predicates on
open records, plus historical process candidates across recorded phases. Process
matches are candidates, not verified approval/current postponement decisions.
Missing source evidence can hide candidates; the audit explicitly limits scope
completeness to recorded local evidence. Report missing/inconsistent data for the
independent ingestion process; do not expand this skill into source repair.

Preparation writes:

- `database_workflow_manifest.json`: project/date selection, selected records,
  original DB identity, snapshot hash, timestamps and paths.
- `database_evidence_snapshot.sqlite3`: selected metadata/comments/history from
  one read transaction; copying it does not refresh the ticket timestamps.
- `selected_ticket_ids.json` and `selected_ticket_scope.json`: frozen selected
  IDs and auxiliary scope metadata, not a prior/final V12 KB dependency.
- `database_source_audit.json`: scope/selection counts, recorded source times,
  empty/unknown evidence and explicit unverified online freshness/completeness.
- `current_state_llm_inputs.jsonl`: the existing versioned cited-analysis inputs,
  with `source_mode=database_snapshot` and a semantic evidence fingerprint.

Reusing the same run preserves the frozen snapshot; changing scope needs a new
run. Never edit its SQLite or JSONL files to reuse a stale model result.

### 2. Run The Existing Cited Model Lane

Use a stable cache outside dated run directories. Keep a separate file for each
model and analysis contract; a new empty JSONL per run discards cache benefits.

```powershell
$inputs = Join-Path $run 'current_state_llm_inputs.jsonl'
$model = 'deepseek-v4-pro'
$cacheRoot = 'exports\idcevo_phase09\llm\shift_tolerate_current'
$summaries = Join-Path $cacheRoot ($model + '_v2_summaries.jsonl')

node scripts\summarizeOpenTicketPostponementInputs.mjs `
  --input $inputs --output $summaries --model $model `
  --concurrency 3 --max-retries 4 --limit 6
if ($LASTEXITCODE -ne 0) { throw 'Model sample failed; inspect it before a full run.' }
```

Inspect dated fixes, retests, retractions/related-ticket claims and recurrence
before continuing. Then run the remainder; matching completed outputs are cached:

```powershell
node scripts\summarizeOpenTicketPostponementInputs.mjs `
  --input $inputs --output $summaries --model $model `
  --concurrency 12 --max-retries 4
if ($LASTEXITCODE -ne 0) { throw 'Model coverage incomplete; do not build a reduced report.' }
```

Use the current `shift-current-evidence-v2` lane, not Phase 09 v7 summaries.
The prompt explicitly interprets database mode as latest recorded offline state,
not a newly verified online state. The same citation, no-invention, independent
status-axis and intact-message-budget rules apply. The runner explicitly bounds
messages to 100,000 characters each/120,000 total and skips the unrelated
dashboard system prompt; do not allow generic chat compaction to cut evidence.

### Cache Reuse And Token Cost

- Reuse requires the same ticket ID, input version, semantic evidence fingerprint
  and model identifier, complete analysis fields, and valid supplied citations.
  A title/ID match alone is insufficient.
- New tickets, changed description/comments/history/phase/planning evidence,
  changed analysis contract or changed model require new inference. Cache hits
  make no analysis-model request.
- `database-analysis-evidence-v2` ignores only fetch-operation timestamps such as
  `fetched_at`/`refreshed_at` when calculating analysis identity. Real creation,
  last-modified and history-event dates remain evidence. A routine re-fetch with
  unchanged evidence does not itself require new analysis.
- The raw SQLite snapshot hash and original fetch timestamps remain separate,
  exact audit metadata. Ignoring operational time for model-cache identity does
  not weaken the immutable-snapshot/content gate.
- A changed input/prompt/schema must have its version/fingerprint updated. Do
  not edit a cached fingerprint to force reuse. Historical repaired-KB inputs
  and the previous full-row database fingerprint policy are not automatically
  interchangeable with this semantic policy; a first migration may miss cache.
  Resuming an obsolete-policy run stops before model work; prepare a new run,
  preserve the old archive, and never edit the manifest to bypass that guard.
- Existing compatible JSONL may seed the chosen cache after preserving the old
  file. Do not extract prose from Excel or silently relabel old model outputs.
- Each new run still rebuilds Excel/JSON/HTML from the current selected scope;
  those native writers do not call an LLM. Tickets leaving scope must disappear
  from the new report even if their old cache entries remain available.
- Report cached versus pending/completed/failed counts from the batch output.
  Do not equate a fresh artifact build with full-model token consumption, or
  claim exact billed tokens/cost when the gateway did not provide usage data.

### Model Routing And Responsibility

Ticket summary, postponement/timing/version analysis, root-cause evidence and
recovery analysis run in the repository's Node model runner through
`requestCompanyChatCompletion`. Copilot orchestrates tools/code; its chat model
is not the ticket batch's analysis engine.

`--model` is explicit in this skill. `deepseek-v4-pro` is the example; changing
`$model` to a supported option such as `glm-5.1` also changes the cache filename.
The configured route is resolved by
[chatModelConfig.mjs](../../../server/chatModelConfig.mjs), with local environment
loading in [loadLocalEnv.mjs](../../../server/loadLocalEnv.mjs). Do not assume a
model selected in the Copilot UI or an environment default overrides `--model`.

On 2026-10-22, configuration resolved DeepSeek V4 Pro and GLM 5.1 to the local
proxy at `http://127.0.0.1:8001`; the proxy health endpoint returned HTTP 200.
That confirms the client route, not a billable upstream model request. Recheck
the effective route/model when configuration changes; the proxy owns its Beacon
upstream routing. Print only model names, URL origins and credential-presence
flags, never keys/access codes/headers or raw environment contents.

### 3. Generate All Native Outputs

```powershell
& .\.venv\Scripts\python.exe scripts\rebuild_shift_current_evidence.py `
  --from-database --run-directory $run --summaries $summaries `
  --output-directory $output
if ($LASTEXITCODE -ne 0) { throw 'Candidate build failed; do not publish partial outputs.' }
```

The build reads the frozen SQLite, not the mutable live DB. It hash-checks the
snapshot, rebuilds inputs, checks saved input equality and exact scope, then
requires all version/fingerprint/citation-valid summaries before any report write.
It reuses `build_shift_full_records`, `apply_current_evidence` and
`build_merged_shift_tolerate_artifact_triple`; there is no second report engine.

Outputs are the FullLLM source workbook, matching V12 Excel/KB JSON/catalog HTML,
and `candidate_build_summary.json` with actual paths/counts. Rows are labeled
`database_snapshot`; the source audit and report never count them as
`current_verified` online tickets. Unknown fetch completeness remains visible.
Use a fresh output directory if Excel is locked, then publish explicitly below.

## Comparison With Phase 09

Compare with [Phase 09 closure audit](../../../.claude/skills/phase09-llm-closure-audit/SKILL.md)
and the [Phase 09 after-sales lineage](../phase09-ticket-to-aftersales-decision-tree/SKILL.md).

| Stage | Shared pattern | Important difference |
| --- | --- | --- |
| Database source | Canonical SQLite resolver and metadata/comments/history | Phase 09 targets concluded-without-action scope; this lane selects project/date-scoped Open Shift and historical process candidates. |
| Payload | Derive facts and preserve evidence before model calls | Phase 09 uses `phase09_idcevo_report`; this lane uses `shift_database_workflow` plus existing ticket/process predicates. |
| Model input | Bounded evidence JSONL and source/version fingerprints | Phase 09 `phase09-llm-summary-v7` and this lane `shift-current-evidence-v2` are independent contracts. |
| Model execution | Existing company gateway, JSONL resume, concurrency, retries and error files | Different runners/prompts and evidence sizes; do not copy the old concurrency-30 assumption or interchange caches. |
| Judgments | No invented root cause, fix, delivery or safety conclusions | Phase 09 audits closure quality; this lane separates historical shift, current postponement, repair and delivery evidence. |
| Scenario/tree generation | Existing Service Function classifiers and native writers | The wider Phase 09 after-sales pipeline has a separate V2 extraction stage; this current-effect lane uses its existing clustered scenario/tree builder, not that cache. |
| Output | Native workbooks, raw evidence and actual-value/count validation | This lane produces FullLLM plus filter-enabled V12 Excel/JSON/HTML directly. |
| Publication | Immutable input/candidate, exact destination, locks and hashes | An HTML-only fix is not a full synchronized triple release. |

Share infrastructure and checks, not the business schema or historical model
results. Existing Phase 09 legacy transport/caches must be checked for intact
evidence delivery before making new current-state claims.

## HTML Filter Contract

- Phase checkboxes default to excluding 05 and 08; all other recorded phases are
  selected. All/Clear/Reset must be reversible.
- Creation-date start/end are UTC and inclusive; initial dates are unrestricted.
  Active date bounds exclude undated tickets. Reject reversed ranges.
- Filter the view only. Excel/JSON retain the full approved scope and record the
  HTML defaults; a date/phase selection must not delete source or scenario data.
- Rebuild node memberships, prune empty branches, and recompute function/global
  counts, scenario tables, selected-node summaries and collapsed evidence. No
  excluded ticket may survive through an old aggregate or cached detail.
- An empty result shows zero counts, no stale nodes/details and an empty state.
- Test high-volume and zero-data functions at desktop and mobile widths. Report
  browser-automation limitations instead of claiming an unverified interaction.

## Validate And Publish

Run the focused offline regressions before delivering a changed workflow:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_octane_ingest_pipeline.py `
  backend/tests/test_shift_database_workflow.py `
  backend/tests/test_shift_tolerate_merged_v12.py `
  backend/tests/test_open_shift_scenario_triple.py `
  backend/tests/test_phase09_all_function_after_sales_catalog.py `
  backend/tests/test_phase09_idcevo_scenario_build.py -q

npm test -- src/test/server/companyChat.test.ts src/test/server/chatMessageBudget.test.ts src/test/server/openTicketPostponementLlmRunner.test.ts
```

Validate actual generated files, not only exit codes: exact unique ticket scope,
current versus historical-unavailable coverage, matching Excel/KB scenario and
ticket totals, native UTC creation-date cells immediately after Ticket ID, real
nonblank analysis values, expected source evidence/chunk counts, filter defaults,
valid tree topology, and browser pruning/counts/details. Do not count `str(None)`
as populated content. Keep filter columns in `01_Scenario_Library` and the compact
traceability layout of `07_Ticket_Mapping`.

Before publication, compare the requested destination against actual outputs.
For a full release, back up and publish all three matching members only after
Excel locks are released; compare corresponding SHA-256 hashes. For an explicitly
requested HTML-only correction, verify unchanged ticket scope, back up the old
HTML, publish the generated HTML atomically to that exact path and hash-check it.
Do not describe an HTML-only correction as a synchronized triple release.

Open/reload the exact final path, not another candidate directory. Verify both
filters, 05/08 default exclusions, date behavior and restored full-view counts.
Report the delivered paths, verified counts, tests and any unresolved coverage or
publication limits. Do not claim the old path changed when only a new directory
was generated.

## Historic Checkpoint, Not A Future Acceptance Target

The 2026-10-20 run retained 1,805 IDs: 1,794 current and 11 online-unreturned
historical records. Full data had 294 plan branches; default phase filtering
showed 1,444 tickets/271 plans; adding `2026-01-01` as the start showed
1,175/255. Recompute these counts for every new snapshot, never hard-code them.
The full filtered triple lives under
`exports/idcevo_phase09/V4/source_repaired_20261020/ticket_filters_v12`.
The user-requested HTML under the sibling `candidate_v12` directory was later
updated separately; its Excel/JSON were not republished in that HTML-only fix.
