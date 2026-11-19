# Contextual Agent Conversation Implementation Plan

> **For agentic workers:** Execute this approved change inline with focused test-first checkpoints. Preserve unrelated worktree changes; do not stage, commit, create a branch, or edit OpenTPM.

**Goal:** Replace keyword-controlled conversation decisions with a context-aware Pi conversation while retaining ontology, semantic tools, actor scope, and evidence protection.

**Architecture:** Every nonempty authenticated conversation can reach the model with its full history and allowed tools. The runtime distinguishes tool-capable conversation instructions from final-answer instructions, preserves model-authored completed responses, and publishes them through the existing guarded SSE boundary. Deterministic ontology plans remain executable business capabilities rather than natural-language rejection rules.

**Tech Stack:** Existing Pi 1.5.2, Node ESM, existing company transport, Vitest; no new dependencies or endpoint changes.

## Task 1: Reproduce The Known Defects

**Files:**

- `src/test/server/agentRuntime/piToolRuntime.test.ts`
- `src/test/server/companyChat.test.ts`
- `src/test/server/answerValidator.test.ts`

- [x] Assert business wording containing Python is not rejected before model interpretation and contextual follow-ups remain eligible for tools.
- [x] Capture a synthetic tool-capable request and reject final-answer-only/never-call-tools instructions in that request.
- [x] Accept Markdown list markers and query-scope dates around supported citations; continue rejecting unsupported counts, records, dates, and causal assertions.

```ts
expect(classifyDirectMainAgentIntent("Python automation testing coverage")).toBeNull();
expect(shouldPlanMainAgentTools(historyWithFollowUp)).toBe(true);
expect(validateAnswerTextCitations(supportedNumberedAnswer).valid).toBe(true);
```

Run the three touched Vitest files and confirm the new assertions fail before implementation.

## Task 2: Remove Keyword Conversation Gates

**Files:**

- `server/mainAgentDirectIntent.mjs`
- `server/mainAgentToolPlanning.mjs`
- `server/mainAgentToolRegistry.mjs`
- Existing runtime/routing tests and golden fixtures affected by the new contract

- [x] Stop classifying ordinary business conversation by greeting/topic/blacklist regexes. Keep server-owned data-access denial independent of natural-language interpretation.
- [x] Admit nonempty conversation messages for model tool choice, including follow-ups and attachment-bearing messages.
- [x] Keep canonical tool schemas, actor checks, denied-plan enforcement, reporter identity grounding, and bounded recovery intact.
- [x] Run focused conversation/routing tests after each implementation change.

## Task 3: Preserve One Conversation Across Tool And Answer Phases

**Files:**

- `server/companyChat.mjs`
- `server/agentRuntime/governedChatRuntime.mjs`
- `server/agentRuntime/piChatRuntime.mjs`
- `server/agentRuntime/langGraphChatHandler.mjs`
- Existing runtime and company-transport tests

- [x] Use a tool-capable system prompt when tools are exposed; use the answer-only prompt only when no tool invocation is possible.
- [x] Retain a completed assistant response instead of discarding it and asking another model call to reinvent the answer.
- [x] Keep required final composition for deterministic/terminal tool execution inside the Pi turn, using governed context and a separate injected answer-completion dependency for deterministic tests.
- [x] Feed prepared responses through the existing sanitizer, evidence/citation validator, and SSE completion boundary. Do not bypass claim protection.
- [x] Prove ordinary conversation uses one model completion, tool results reach follow-up requests, and a prepared response is not generated twice.

## Task 4: Validate Claims Without Treating Layout As Data

**Files:**

- `server/answerValidator.mjs`
- `src/test/server/answerValidator.test.ts`

- [x] Remove only Markdown list-marker numbers from numeric claim inspection.
- [x] Recognize dates only when supported by the actual query scope/provenance; do not exempt arbitrary dates or values.
- [x] Preserve negative tests for unknown citations, unsupported numbers/records, scope/evidence failure, and causality.

## Acceptance And Live Verification

- [x] Run the touched conversation, runtime, transport, policy, evidence, and auth suites; run required scoped qualifications twice.
- [x] Run focused lint, syntax checks, diagnostics, and the production build.
- [x] Obtain an independent read-only review and fix reproducible defects.
- [x] Restart only the verified owned Vizion stack, keeping the model proxy and OpenTPM unchanged.
- [x] Verify ordinary text conversation, a business query, and a contextual follow-up through the actual Chat endpoint. Record real results separately from synthetic tests.

## Results

Implemented and verified locally on 2026-11-18. OpenTPM, model endpoints, provider credentials, dependencies, and upstream ticket sources were not changed.

### Additional Findings And Fixes

- The old semantic candidate classifier could not express ordinary conversation and manufactured an analysis plan for a greeting. Pi now requests capability-only context with `interpretQuery:false`, including ontology versions, canonical metrics, dimensions, and existing mandatory scope filters. Explicit compatibility callers retain the original interpretation path.
- Several backend operations advertised by the old tool registry were not mounted. A bounded, cached OpenAPI readiness check now filters only canonical tools using a fixed route/method map. It grants no permissions and creates no tools from arbitrary metadata.
- Restored the existing semantic defect payload provider and semantic query/record routes, plus the auth-router mount. Reused the existing query, snapshot, semantic executor, and evidence store contracts; retained revision checks and mandatory row scope. No upstream synchronization was run.
- Model drafts could still place unsupported numbers outside citations. Pi permits one evidence-only, no-tool correction; if that fails, a deterministic summary may contain only actual verified semantic values, dates, identifiers, and warnings, with the same final citation validation. A failed evidence gate still blocks publication.
- Same-scope history may ground follow-ups, but a failed current semantic attempt cannot inherit old success. Review-driven regressions also cover numbered headings, unsupported historical claims, and unsupported factual text without digits.

### Automated Verification

| Check | Final Result |
| --- | --- |
| Conversation/runtime/context/transport/auth/policy/semantic combination | 18 files, 215 passed in each of two consecutive runs |
| Official `npm run test:agent-evals` | 7 files, 28 passed |
| Semantic payload/query/store/goldens/auth/import Python slice | 55 passed |
| Focused TypeScript ESLint | Passed with zero warnings |
| Server entry-point syntax and touched-file diagnostics | Passed |
| `npm run build` | Passed; existing Vite configuration and bundle-size warnings remain |
| Final independent read-only review | No new Critical or Important findings in the reviewed slice |

### Live Verification

Restarted only the owned Vizion development stack; `/health` reported the actual `pi` runtime. Final acceptance used fresh per-run thread/audit IDs while preserving history within the greeting/query/follow-up conversation. This avoids counting a previous valid answer as a new query.

| Case | Observed Final Result |
| --- | --- |
| Greeting | Completed answer, no unnecessary tools |
| Business count question | Actual business tool call and completed audit |
| Contextual short follow-up | Actual business tool call using the ongoing conversation |
| Python testing coverage question | Reached model interpretation and business capability tools, without keyword rejection |
| Explicit ontology metric query | Actual `resolve_business_terms` and `query_semantic_metrics` calls; final citation validation passed with no violations |

All five final live cases returned HTTP 200, nonempty content, `[DONE]`, completed run audits, and no error events. A separate final-version semantic probe also passed. A safe cited evidence summary is an acceptable result; these checks do not certify every model-generated analytical statement or the readiness of every legacy backend operation.

The application remains available at `http://127.0.0.1:8080`. No improvement in microphone transcription, real SSO authentication, or model-provider reliability is claimed by this text-conversation change.

## User-Approved Revision: Advisory Answer Diagnostics

The subsequent user report reproduced a blocked reporter follow-up after choosing `detected_by`. The user explicitly requested removal of these conversation restrictions while retaining semantic and ontology capabilities. This revision supersedes the hard answer-publication and forced-correction requirements above, not the backend's authorization or semantic execution contracts.

**Goal:** Publish the model's answer without evidence-format vetoes, forced regeneration, synthetic citation insertion, or deterministic replacement. Preserve diagnostic events, honest tool results, ontology, semantic queries, actor scope, and canonical argument validation.

**Architecture:** Answer checks observe rather than authorize publication. Pi ends when it has a completed answer. Evidence and citation context become nonblocking guidance, while backend access checks remain authoritative. No dependencies, credentials, model endpoints, branches, or commits are changed.

### Revision Checkpoints

- [x] Reproduce suppressed answers, lost usage, delayed streaming, injected citations, and inspection-buffer blocking in `src/test/server/companyChat.test.ts`.
- [x] Remove the publishing gate in `server/companyChat.mjs`; retain bounded diagnostics and protocol sanitation. All 25 transport tests passed, including diagnostic-exception isolation.
- [x] Reproduce and remove forced correction/replacement in `server/agentRuntime/piChatRuntime.mjs` and the unused shared-stage helpers in `server/agentRuntime/governedChatRuntime.mjs`.
- [x] Change evidence/citation prompt context to advisory guidance without changing ontology registry, semantic execution, or data authorization.
- [x] Run the scoped Node qualification twice, agent evaluations, lint, diagnostics, and build. Recheck backend policy contracts if their shared tool paths are affected.
- [x] Restart only the owned Vizion stack and replay the reporter clarification with fresh audit IDs. Record actual data/tool failures separately from answer availability.

### Revision Verification Results

- Scoped Node combination: 19 files, 224 passed in each of two consecutive runs. Official Agent evaluations: 28 passed. Retained Python semantic/auth/import contracts: 55 passed.
- Focused ESLint, touched-code diagnostics, and production build passed. Existing Vite configuration and bundle-size warnings remain.
- Independent read-only review found no new authorization, canonical-argument, advisory-validation, or diagnostic-exception blocker. It separately noted the existing CRLF upstream-frame parsing limitation; that unrelated parser behavior was not changed in this revision.
- Restarted the verified owned Vizion stack. The actual Chat endpoint reports Pi and serves the new nonblocking contract.
- Original two user messages were replayed with fresh thread/audit IDs. Both returned nonempty answers and `[DONE]`, without the old system refusal. The model still asked for optional report/time preferences; that is model conversation behavior, not a publication veto.
- An explicit all-scope reporter request actually called `query_semantic_metrics` and returned a normal explanation of a failed tool call, not a generic publication refusal. This is not a successful personal-defect query: `detected_by` exists in `ontology/v1/entities.json` but is not declared in `ontology/v1/dimensions.json`. No ontology mapping was added or bypassed.
- A separate real query of supported metric `defect.created_count` succeeded. The requested plain-text answer was fully published with `answer-validation.valid:false`, `blocking:false`, and citation/numeric diagnostics. This directly confirms that mandatory citation formatting no longer controls publication.
- No model endpoints, provider credentials, dependencies, ontology definitions, backend permissions, or upstream sources were changed. No claim is made that removing answer checks fixes missing data mappings or makes every model assertion correct.

Runtime target:

```js
finishTurn: () => {
  if (planningError || state.preparedCompletion) return { action: "end" };
  if (state.toolExecutionMode === "deterministic" && !state.stoppedReason) state.stoppedReason = "deterministic_plan_complete";
  if (state.toolStepIndex >= maxSteps && !state.stoppedReason) state.stoppedReason = "max_steps";
  return state.stoppedReason || !plannedCalls.length ? { action: "continue" } : undefined;
}
```

Focused checks:

```powershell
npm test -- src/test/server/companyChat.test.ts
npm test -- src/test/server/agentRuntime/piToolRuntime.test.ts -t "history-based answer diagnostics|does not regenerate|failed current semantic attempt"
```

Expected contract: answer text and usage reach SSE even when `answer-validation.valid` is false; `blocking:false` identifies its advisory role. Model honesty and data quality still matter: this intentionally removes the programmatic guarantee that every published claim matches an evidence envelope.

## 2026-11-19: Model-Owned Business Tool Conversation

The user approved optimization after comparison with OpenTPM. Keep OpenTPM read-only and preserve existing ontology, semantic execution, authorization, and unrelated worktree changes. Execute locally with test-first checkpoints; do not install dependencies, create branches, commit, or synchronize upstream data.

**Goal:** Let the model select available tools and correct ordinary tool errors instead of forcing named-reporter workflows or ending the conversation prematurely.

**Architecture:** Pi owns the conversation loop. Backend tools own capabilities, typed argument checks, and authorization. Ordinary tool failures return useful structured observations to the model; actual access denial remains enforced. Reuse existing business execution functions before removing or consolidating their tool wrappers.

### Optimization Checkpoints

- [x] Reproduce the reporter workflow selecting an unavailable tool before the model. In `src/test/server/agentRuntime/piToolRuntime.test.ts`, provide semantic tools but no reporter lookup, and assert the model receives those tools without a forced execution.
- [x] Remove the reporter-specific selection and pre-model script from the Pi path in `server/mainAgentToolPlanning.mjs` and `server/agentRuntime/governedChatRuntime.mjs`. Preserve explicit compatibility callers where needed rather than breaking unrelated APIs.
- [x] Reproduce recoverable schema/value/tool errors ending Pi exploration. Return errors to the model for a corrected permitted call, while preventing unauthorized execution and retaining bounded turns.
- [x] Simplify planning guidance so registered schemas, actual available tools, and full conversation drive selection rather than fixed page-specific recipes. Verify any execution-budget change with a scoped regression.
- [x] Check the existing discovery/query tool integration for missing backend capabilities and fix only verified local wiring defects needed for the approved workflow; do not invent ontology dimensions or weaken row scope.
- [x] Run scoped tests after each edit, then repeat the final conversation/auth/semantic qualification twice, lint, diagnostics, and build. Review the completed slice and activate only the owned local stack.
- [x] Replay an actual reporter question and a recoverable tool error with fresh audit IDs. Report data access success separately from normal answer availability.

### Optimization Results

- Pi now selects from actual available tools without the reporter-specific allowlist, forced reporter sequence, automatic empty-result diagnosis, or fixed error-retry script. Full conversation and tool observations drive the next model choice. Explicit authorization denial, typed argument validation, actor-isolated history, unique call IDs, and bounded execution remain enforced.
- Default Pi budget is eight tool rounds, capped at ten. Explicit canonical plans and LangGraph rollback remain compatible. The existing execution tools were reused, not replaced with arbitrary SQL or code access; no claim is made that the public tool surface has already been consolidated into three tools.
- Restored existing backend API wiring for ontology/field/candidate discovery, scoped defect aggregate/records/export, team analysis, legacy read-only fallback, and legacy testcase context. Signed candidate discovery enforces row scope. Unscoped legacy tools remain denied for OIDC actors.
- Existing source-name lookup and typed analytics now support real reporter filtering and grouping; no person-name keyword rule or ontology dimension was added. Unspecified agent dates no longer inherit the Dashboard year, while supplied years/windows remain unchanged. Review caught two sibling date-schema mismatches; all three query interfaces now have focused all-time tests.
- First live attempts exposed a real field-discovery stall, not a model outage: the interactive loader was performing per-column full-table population scans. It now uses bounded sampling, clearly labels sampled metadata, and leaves explicit CLI profiling exact. A deterministic test rejects either full-count helper on the interactive path.

### Final Verification

| Check | Result |
| --- | --- |
| Node conversation/tools/auth/semantic combination | 20 files, 274 passed in each of two runs |
| Backend routes/scope/records/export/semantic/catalog combination | 123 passed in each of two final runs |
| Official Agent evaluation entry point | 28 passed |
| Focused ESLint and touched-file diagnostics | Passed |
| Production build | Passed with existing Vite/bundle warnings |
| Read-only review | Reported date-schema mismatch reproduced and fixed; subsequent bounded-catalog review found no Critical or Important blocker |

### Final Live Observations

- The owned Vizion stack was restarted with final code; OpenTPM, the independent model proxy, credentials, dependencies, ontology definitions, and upstream ticket sources were unchanged.
- Backend discovery returned all 23 canonical tools as registered/available, up from 12 before wiring repairs. This is operation availability, not permission to bypass actor checks or proof that every dataset is populated.
- The repaired field-search endpoint returned five candidates in 216 ms and identified its source profiling as sampled. Earlier requests exceeding 180/240 seconds were not counted as passes; their old processes were stopped when loading the bounded fix.
- The original three-turn conversation was replayed with fresh thread/audit IDs: the initial ambiguous question received a normal clarification; the detected-by reply triggered model-selected candidate discovery and aggregation; the ECU/details follow-up triggered actual aggregate and signed record tools.
- The model resolved the supplied name to Miao Xu and returned 151 associated defects in the current local authorized data. It then retrieved ECU aggregation and record examples. All three final run audits completed, all responses had nonempty content and `[DONE]`, and no SSE error or old publication-refusal message occurred.
- The final clarified query took about 77 seconds; the detailed ECU follow-up took about 132 seconds and included repeated record-tool attempts before returning data. This is functional completion, not a latency or universal model-accuracy claim. Argument/value recovery also has isolated failure-injection tests; not every attempted tool call is described as a successful data result.
- Application URL: `http://127.0.0.1:8080`. Existing semantic dimension coverage, real SSO, microphone accuracy, provider latency, and unrelated stream-parser behavior are not claimed fixed.

First discriminating check:

```powershell
npm test -- src/test/server/agentRuntime/piToolRuntime.test.ts -t "model handle a reporter question"
```

Expected result after the first implementation: `requestToolCompletion` is called with `query_semantic_metrics` and `query_semantic_records`; the unavailable `search_analytics_filter_values` is never forced by the harness.
