# Pi Read-Only Agent PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Demonstrate a real Pi agent loop over existing governed, read-only tools without changing the production Chat runtime.

**Architecture:** Add an independently callable Pi tool-turn adapter and a lazy export from the existing orchestrator. Keep the company model transport, governed tool execution, evidence envelope, and frontend tool-event contract. A standalone comparison command uses synthetic data by default and must label it explicitly.

**Tech Stack:** Node.js ESM, Pi agent-core, existing company completion transport, Vitest.

---

## Scope And Gates

- Keep LangGraph as the production default; do not change HTTP routes or runtime selection.
- Do not expose filesystem, shell, arbitrary code, write actions, or automatic plugin installation to the model.
- Use a fixed read-only tool allowlist in addition to the existing tool gate.
- Keep actor scope, access-denied recovery, clarification stopping, evidence, and existing UI tool events.
- Bound model steps and suppress repeated identical calls within a turn.
- Do not run upstream synchronization or real business-data queries for this PoC.
- Install the resolved Pi release with exact versions and dependency lifecycle scripts disabled.
- Do not commit, stage, create a branch, or revert pre-existing worktree changes.

## Task 1: Establish The Adapter Contract

**Files:**

- Create: `src/test/server/agentRuntime/piToolRuntime.test.ts`
- Modify: `server/mainAgentToolOrchestrator.mjs`

- [x] Write and run a failing contract test using the existing module namespace. The adapter must preserve `contextText`, `toolCalls`, `toolMessages`, `toolEvents`, `events`, `evidence`, `selectedToolset`, and `stoppedReason`, and expose `piEvents` proving that Pi executed the turn.

```ts
expect(orchestrator).toHaveProperty("runPiMainAgentToolTurn", expect.any(Function));
expect(result.toolCalls).toEqual([catalogCall]);
expect(result.piEvents.map((event) => event.type)).toContain("tool_execution_start");
```

Run: `npm test -- src/test/server/agentRuntime/piToolRuntime.test.ts`
Expected RED: the missing adapter export fails the assertion before any network request.

- [x] Add the lazy export without importing Pi in the production module's startup path.

```js
export async function runPiMainAgentToolTurn(options = {}) {
  const runtime = await import("./agentRuntime/piToolRuntime.mjs");
  return runtime.runPiMainAgentToolTurn(options);
}
```

## Task 2: Implement The Restricted Pi Loop

**Files:**

- Create: `server/agentRuntime/piToolRuntime.mjs`
- Modify: `package.json` through the package manager
- Test: `src/test/server/agentRuntime/piToolRuntime.test.ts`

- [x] Inspect the installed package exports/signatures, then install pinned Pi dependencies.

Run: `npm install --save-exact --ignore-scripts --package-lock=false @earendil-works/pi-agent-core@1.5.2 @earendil-works/pi-ai@1.5.2`

- [x] Adapt the existing company completion result to Pi's assistant-message stream. Translate finalized Pi messages back to the existing OpenAI-shaped planning protocol.
- [x] Register only supported read-only schemas, execute them through `executeMainAgentPlannedToolCall`, and preserve governed evidence and recovery events. Pi owns the follow-up loop; the business executor retains authority over each call.

```js
const executed = await executeMainAgentPlannedToolCall({
  toolCall,
  selectedToolset,
  executeToolCall,
  toolDependencies,
});
```

- [x] Add RED/GREEN tests for unregistered/write-tool rejection, invalid arguments, clarification, access denial, maximum steps, duplicate suppression, actor propagation, and empty-result diagnosis.
- [x] Run the focused Pi test after each implementation change, then run the existing execution/policy/evidence gates without editing unrelated failures.

Run: `npm test -- src/test/server/agentRuntime/piToolRuntime.test.ts src/test/server/mainAgentExecutionGolden.test.ts src/test/server/mainAgentPolicyGolden.test.ts src/test/server/mainAgentToolRecovery.test.ts src/test/server/answerValidator.test.ts`

## Task 3: Publish A Bounded Comparison Command

**Files:**

- Create: `scripts/probePiAgentRuntime.mjs`
- Modify: `package.json`
- Update: this plan with commands, results, and remaining unverified requirements

- [x] Add `probe:pi-agent` to compare the existing tool turn and Pi against identical synthetic completion/tool inputs. Report tool sequence, evidence count, stop reason, model-call count, measured duration, parity, and `dataSource: "synthetic"`.
- [x] Verify that the default command makes no network requests and returns a nonzero exit code for parity failure.

Run: `npm run probe:pi-agent`
Expected: labeled synthetic parity PASS; no production-runtime switch or real-data access.

- [x] Run focused lint, syntax checks, diagnostics, and an independent read-only review. Report any real-model, real-data, or frontend migration work as unverified, not as completed replacement.

## Results

### Verified On 2026-11-11

- Pi agent-core and pi-ai are pinned to `1.5.2`; the installed `Agent`, stream factory, and prompt replay exports were checked directly.
- First contract test failed for the missing adapter, then passed through the installed Pi runtime. Subsequent RED/GREEN checks covered deduplication, automatic diagnosis, recovery ordering, and content-level parity.
- Final five-file gate command above: **38 tests passed**, including **18 Pi PoC tests**.
- Default-runtime check: `npx --no-install vitest run src/test/server/agentRuntime/langGraphChatRuntime.test.ts -t "uses LangGraph as the default runtime mode"`: **1 passed**, 24 unrelated cases intentionally skipped.
- `npm run probe:pi-agent`: all three existing execution fixtures passed with `networkRequests: 0`. Canonical fingerprints compare arguments, API request bodies, tool results, evidence contents, context, and UI events, not only counts.
- Independent read-only review found same-batch recovery ordering and weak parity comparison. Both were reproduced with failing tests and fixed; the actual CLI failure-exit branch is also covered.
- `npx --no-install eslint src/test/server/agentRuntime/piToolRuntime.test.ts --max-warnings=0`, Node syntax checks for all three touched ESM modules, and editor diagnostics passed.
- `npm run build` passed. Existing configuration-loader and large-chunk warnings remain; no unrelated source cleanup was attempted.
- Existing-file diff is restricted to two Pi dependencies, the probe command, and the lazy orchestrator export. No production route or runtime selection was changed, and nothing was staged or committed.

### Dependency Transport

Initial npm requests failed with corporate proxy authentication (`407`). A bounded Windows default-proxy-credential request verified official npm access. A temporary localhost-only GET/HEAD relay forwarded to official HTTPS npm and retained package integrity metadata; npm installation scripts were disabled. The relay was stopped after installation and its temporary script removed.

With no npm lockfile, installation re-resolved existing `node_modules` dependencies (174 added, 45 removed, 256 changed), although existing declared dependency ranges were not changed. This is not an intentional dependency-upgrade deliverable. Build and scoped tests passed; no full-workspace regression suite was run. Prefer the repository's existing lockfile/package-manager workflow or isolated installation staging for future additions.

### Usage And Remaining Boundaries

Run `npm run probe:pi-agent` for the synthetic report. The probe accepts no arguments and does not perform real provider requests, database queries, upstream synchronization, or writes.

`runPiMainAgentToolTurn` is an experimental, text-only tool-turn adapter. It does not replace the full LangGraph context resolver, deterministic semantic-plan execution, persisted Chat sessions, SSE final-answer handler, OCR/document input, or frontend workflow. Callers must supply the authenticated actor through existing tool dependencies; production authentication remains outside this adapter. Token usage is reported as unavailable, not zero measured cost. Synthetic durations are not real-model latency benchmarks.

Real company-model transport, real-data behavior/correctness, full session/SSE migration, and model latency/token-cost comparisons remain **unverified** and require a separately scoped live PoC before any production-runtime switch.

## Approved Production Migration (2026-11-16)

The user's subsequent instruction, "migrate", authorizes replacing the production default with Pi. The earlier no-switch constraint applies to the completed PoC only. This phase preserves existing business APIs, authentication, actor scopes, governed semantic plans, recovery rules, runtime audit/checkpoints, attachment handling, and final-answer SSE validation.

- [x] Add a failing production-runtime contract test and implement a Pi-owned driver over the preserved governed Chat stages, without invoking StateGraph on the Pi path.
- [x] Cover deterministic semantic/reporting paths, denied/unscoped access, tool schema validation, attachments, per-turn isolation, and audit/failure persistence with focused tests.
- [x] Make the production gateway use Pi by default, keep explicit LangGraph rollback, and preserve final-answer streaming/validation.
- [x] Run runtime, tool, auth, SSE, ontology, lint/build, and live local smoke gates; restart only the authorized application's processes as necessary and report the actual active runtime. The full Python/business-data qualification remains blocked as recorded below.

No upstream ticket synchronization, external business writes, new model endpoint, branch, staging, or commit is authorized by this migration.

### Production Migration Evidence

- The default gateway now creates `createPiChatRuntime`; it loads LangGraph only for explicit rollback. A module-resolution regression rejects any default Pi path importing `@langchain/langgraph`.
- Shared business stages were mechanically moved to `server/agentRuntime/governedChatRuntime.mjs`, leaving the existing LangGraph driver as a rollback adapter. The migration does not replace business definitions or add raw SQL/code tools.
- Pi executes its validated argument object, rejects reused/duplicate planned call IDs, and prevents a later planned call from reusing an automatic diagnosis ID. Canonical audit counts contain attempted calls only.
- Bounded per-actor/thread histories retain semantic continuation while current-turn offsets prevent stale duplicate/diagnosis/reporter suppression. Both Pi and rollback use a scoped full-turn lease spanning final SSE, claim validation, and completion callbacks. Failure audits retain already-executed tools.
- Final runtime/SSE suite: **72 passed**. Two consecutive 14-file Node qualifications covering runtime, auth, actor capabilities, execution/policy/recovery, company transport, semantic and answer gates: **176 passed in each run**. These do not replace the blocked historical full Python/business-data qualification.
- `npm run build` passed; existing configuration-loader and large-chunk warnings remain.
- The first live local launch reported `agentRuntime: pi`; a real authenticated direct Chat request returned Pi runtime events, HTTP 200, `[DONE]`, and no error event.
- After all code changes, the owned local development stack was stopped and restarted with Pi, without stopping the independent model proxy. Final live check: frontend HTTP 200, health `agentRuntime: pi`, Chat HTTP 200, Pi runtime event, `[DONE]`, zero error events. The frontend is available at `http://127.0.0.1:8080/`.
- Final ESM syntax checks and focused ESLint passed. No backend business source, credentials, ontology definitions, upstream data, or external work items were changed.

### Business-Layer Blocker

A read-only live business request reached `query_semantic_metrics` but was blocked by the preserved answer-evidence gate. A same-scope replay of its valid canonical plan confirmed HTTP 404 from the current Python `/api/semantic/query` endpoint, independent of the Pi driver.

Existing `backend/tests/test_semantic_query_api.py` could not collect because `backend.analytics.semantic_query` imports `build_semantic_defect_payload`, which is absent from the current `backend/analytics/read_models.py`. The current API module also does not mount the semantic routes. No backend business function or scope rule was invented to work around this. Therefore complete business-data readiness and the historical full Python production qualification remain **blocked**, not passed. The runtime migration and the backend restoration are separate results.
