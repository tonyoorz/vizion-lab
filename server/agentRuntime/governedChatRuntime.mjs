import { isOidcScopedActor } from "../agentAuth.mjs";
import { resolveAiAnalyticsContext } from "../aiAnalyticsContext.mjs";
import { extractLatestUserQuery, resolveAiDefectContext } from "../aiContext.mjs";
import { buildCitationContractContext } from "../answerValidator.mjs";
import { requestCompanyChatCompletion } from "../companyChat.mjs";
import { isAnalyticsActorScopeConfigured } from "../internalActorScope.mjs";
import { buildDataAccessUnavailableResponse, classifyDirectMainAgentIntent } from "../mainAgentDirectIntent.mjs";
import {
  buildSemanticContinuationContext,
  evaluateSemanticEvidence,
  formatSemanticEvidenceGate,
} from "../mainAgentEvidence.mjs";
import { buildRuntimeRunSummary } from "./runSummary.mjs";
import {
  buildDefectReporterAggregateToolCall,
  buildDefectReporterAliasLookupToolCall,
  buildDefectReporterClarificationToolCall,
  buildDefectReporterLookupToolCall,
  buildEmptyDiagnosisToolCall,
  buildSelectedToolsetContext,
  buildToolPlanningContext,
  executeMainAgentPlannedToolCall,
  hasEmptyAnalyticsResult,
  hasPlannedDiagnosis,
  selectMainAgentToolset,
  shouldPlanMainAgentTools,
} from "../mainAgentToolPlanning.mjs";
import { isToolAllowed, selectMainAgentToolset as selectModelToolset } from "../mainAgentToolRegistry.mjs";
import {
  buildCatalogBackedAnalyticsRetry,
  buildGovernedSemanticPlanRetry,
  completeCatalogBackedAnalyticsRetry,
} from "../mainAgentToolRecovery.mjs";
import { createOntologyRegistry } from "../ontology/registry.mjs";
import { validateGovernedAnalysisPlan } from "../ontology/analysisPlanner.mjs";
import { fingerprintQueryPlanSteps, validatePlan } from "../ontology/queryPlanner.mjs";

const SUPPORTED_RUNTIME_MODES = new Set(["pi", "langgraph"]);

export function wrapScopedChatRuntime({ kind, prepareInvocation, invoke }) {
  const pendingTurns = new Map();
  function runChatTurn(input, run) {
    const initial = prepareInvocation(input);
    const key = JSON.stringify([initial.threadId, initial.actorScope]);
    const leased = { kind, invoke: (request, options = {}) => invoke(request, { ...options, initial }) };
    const turn = (pendingTurns.get(key) || Promise.resolve()).then(() => run(leased));
    const settled = turn.then(() => undefined, () => undefined).finally(() => {
      if (pendingTurns.get(key) === settled) pendingTurns.delete(key);
    });
    pendingTurns.set(key, settled);
    return turn;
  }
  return {
    kind,
    invoke: (input = {}, options = {}) => runChatTurn(input, (leased) => leased.invoke(input, options)),
    runChatTurn,
  };
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function normalizeThreadId(body, now) {
  const explicit = [body?.threadId, body?.conversationId, body?.sessionId].find(isNonEmptyString);
  if (explicit) {
    return String(explicit).trim();
  }
  return `chat-${now().getTime().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeRunId(body, now) {
  if (isNonEmptyString(body?.runId)) {
    return String(body.runId).trim();
  }
  return `run-${now().getTime().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function stringList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function normalizeActorScope(body) {
  const actor = body?.actor && typeof body.actor === "object" ? body.actor : body?.actorScope && typeof body.actorScope === "object" ? body.actorScope : {};
  const rawScopes = actor.scopes && typeof actor.scopes === "object" ? actor.scopes : {};
  const scopes = {};
  for (const key of ["workspaceIds", "projectIds", "teamIds", "allowedObjectTypes", "allowedPropertyIds", "rowPolicyIds", "sensitiveFieldPolicyIds"]) {
    const values = stringList(rawScopes[key]);
    if (values.length) {
      scopes[key] = values;
    }
  }
  return {
    actorId: String(actor.actorId || body?.userId || body?.user?.id || "").trim(),
    scopeHash: String(actor.scopeHash || "").trim(),
    ...(Object.keys(scopes).length ? { scopes } : {}),
  };
}

function hasActorScope(actorScope) {
  return Boolean(actorScope?.actorId || actorScope?.scopeHash || Object.keys(actorScope?.scopes || {}).length);
}

function cloneEventForObserver(event) {
  if (!event || typeof event !== "object") return event;
  if (typeof globalThis.structuredClone === "function") return globalThis.structuredClone(event);
  return JSON.parse(JSON.stringify(event));
}

function emit(config, event) {
  config?.configurable?.onEvent?.(cloneEventForObserver(event));
  return event;
}

function createDefaultOntologyRegistry() {
  try {
    return createOntologyRegistry();
  } catch {
    return null;
  }
}

function ensureAbortSignalCompatibility() {
  const prototype = globalThis.AbortSignal?.prototype;
  if (!prototype || typeof prototype.throwIfAborted === "function") {
    return;
  }
  Object.defineProperty(prototype, "throwIfAborted", {
    value() {
      if (this.aborted) {
        throw new Error("LangGraph run aborted");
      }
    },
  });
}

function mergeContext(...parts) {
  return parts.filter(Boolean).join("\n\n");
}

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (isRecord(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function toolCallSignature(toolCall) {
  const toolName = String(toolCall?.function?.name || "").trim();
  if (!toolName) {
    return "";
  }
  const rawArguments = toolCall?.function?.arguments;
  if (typeof rawArguments === "string") {
    try {
      return `${toolName}:${canonicalJson(JSON.parse(rawArguments))}`;
    } catch {
      return `${toolName}:${JSON.stringify(rawArguments)}`;
    }
  }
  return `${toolName}:${canonicalJson(rawArguments ?? {})}`;
}

function removeRepeatedToolCalls(plannedToolCalls, priorToolCalls) {
  const signatures = new Set((Array.isArray(priorToolCalls) ? priorToolCalls : [])
    .map(toolCallSignature)
    .filter(Boolean));
  let removedDuplicate = false;
  const uniqueToolCalls = [];

  for (const toolCall of Array.isArray(plannedToolCalls) ? plannedToolCalls : []) {
    const signature = toolCallSignature(toolCall);
    if (signature && signatures.has(signature)) {
      removedDuplicate = true;
      continue;
    }
    if (signature) {
      signatures.add(signature);
    }
    uniqueToolCalls.push(toolCall);
  }

  return { uniqueToolCalls, removedDuplicate };
}

function parseToolCallArguments(toolCall) {
  const rawArguments = toolCall?.function?.arguments;
  if (isRecord(rawArguments)) {
    return rawArguments;
  }
  try {
    const parsed = JSON.parse(String(rawArguments || "{}"));
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function isCompletedTopIssueGrowthRank(toolCall, result) {
  if (toolCall?.function?.name !== "query_analytics" || hasEmptyAnalyticsResult(toolCall, result)) {
    return false;
  }
  let payload = {};
  try {
    payload = JSON.parse(String(result?.toolMessage?.content || "{}"));
  } catch {
    return false;
  }
  if (payload?.ok !== true) {
    return false;
  }

  const query = parseToolCallArguments(toolCall);
  const dimensions = Array.isArray(query.dimensions) ? query.dimensions : [];
  const derivedMetrics = Array.isArray(query.derived_metrics) ? query.derived_metrics : [];
  const orderBy = Array.isArray(query.order_by) ? query.order_by : [];
  const ordersByDeltaDescending = orderBy.some((order) => order?.field === "delta" && order?.direction === "desc");
  return query.dataset === "defects"
    && query.intent === "rank"
    && dimensions.includes("business_module")
    && derivedMetrics.includes("delta")
    && derivedMetrics.includes("growth_pct")
    && ordersByDeltaDescending;
}

function buildReadySemanticPlanToolCalls({ analyticsContext, actorScope, selectedToolset } = {}) {
  const analysisPlan = governedAnalysisAuditFromContext(analyticsContext);
  const queryPlan = analyticsContext?.queryPlan;
  const actorScopeHash = String(actorScope?.scopeHash || "");
  if (
    analysisPlan?.status !== "ready"
    || queryPlan?.status !== "valid"
    || !actorScopeHash
    || String(queryPlan?.actorScopeHash || "") !== actorScopeHash
    || String(analysisPlan?.sourcePlanId || "") !== String(queryPlan?.planId || "")
  ) {
    return [];
  }

  const semanticSteps = (Array.isArray(queryPlan?.steps) ? queryPlan.steps : [])
    .filter((step) => isRecord(step?.canonicalArgs)
      && isRecord(step?.canonicalArgs?.query)
      && ((step?.operation === "semantic_metric_query" && step?.toolName === "query_semantic_metrics")
        || (step?.operation === "semantic_record_query" && step?.toolName === "query_semantic_records")));
  if (semanticSteps.length !== 1) {
    return [];
  }

  const step = semanticSteps[0];
  if (!isToolAllowed(step.toolName, selectedToolset)) {
    return [];
  }
  const stepId = String(step?.stepId || "").trim();
  if (!stepId) {
    return [];
  }
  return [{
    id: `${queryPlan.planId}-${stepId}`,
    type: "function",
    function: {
      name: step.toolName,
      arguments: JSON.stringify(step.canonicalArgs),
    },
  }];
}

function toolAuditsFromContext(mainAgentToolContext, { runId, threadId, actorScope }) {
  const byId = new Map();
  for (const event of mainAgentToolContext?.toolEvents || []) {
    const toolCallId = String(event?.toolCallId || "");
    if (!toolCallId) {
      continue;
    }
    const current = byId.get(toolCallId) || { runId, threadId, actorScope, toolCallId, toolName: String(event?.toolName || "") };
    if (event.type === "tool-input-available") {
      current.input = event.input;
    }
    if (event.type === "tool-output-available") {
      current.outputSummary = event.outputSummary;
    }
    if (event.type === "tool-recovery") {
      current.recovery = event.recovery;
      delete current.input;
    }
    if (event.toolName) {
      current.toolName = String(event.toolName);
    }
    byId.set(toolCallId, current);
  }
  return [...byId.values()].map((audit) => {
    if (audit.recovery) {
      delete audit.input;
    }
    return audit;
  });
}

function toolLifecycleEventsFromContext(mainAgentToolContext, { runId, threadId }) {
  const events = [];
  const intent = mainAgentToolContext?.selectedToolset?.intent || "unknown";
  for (const event of mainAgentToolContext?.toolEvents || []) {
    if (event.type === "tool-input-available") {
      events.push({
        runId,
        threadId,
        type: "agent.tool.started",
        toolCallId: String(event.toolCallId || ""),
        toolName: String(event.toolName || ""),
        intent,
      });
    }
    if (event.type === "tool-output-available") {
      events.push({
        runId,
        threadId,
        type: "agent.tool.completed",
        toolCallId: String(event.toolCallId || ""),
        toolName: String(event.toolName || ""),
        intent,
        stoppedReason: "",
      });
    }
    if (event.type === "tool-blocked") {
      events.push({
        runId,
        threadId,
        type: "agent.tool.blocked",
        toolCallId: String(event.toolCallId || ""),
        toolName: String(event.toolName || ""),
        intent,
        stoppedReason: String(event.reason || "tool_not_allowed"),
      });
    }
  }
  return events;
}

function governedAnalysisAuditFromContext(analyticsContext) {
  const candidate = analyticsContext?.analysisPlan || analyticsContext?.shadowObservation?.analysisPlan;
  if (!candidate || typeof candidate !== "object") return null;
  try {
    const plan = validateGovernedAnalysisPlan(candidate);
    return {
      schemaVersion: plan.schemaVersion,
      analysisPlanId: plan.analysisPlanId,
      sourcePlanId: plan.sourcePlanId,
      sourcePlanFingerprint: plan.sourcePlanFingerprint,
      ontologyVersion: plan.ontologyVersion,
      schemaFingerprint: plan.schemaFingerprint,
      status: plan.status,
      operation: plan.operation,
      visualization: plan.visualization,
      maxRows: plan.maxRows,
      guardrails: [...plan.guardrails],
      ...(Array.isArray(plan.ruleCodes) ? { ruleCodes: [...plan.ruleCodes] } : {}),
      ...(Array.isArray(plan.ruleEffects) ? { ruleEffects: [...plan.ruleEffects] } : {}),
    };
  } catch {
    return null;
  }
}

async function persistRuntimeState(runtimeStore, state) {
  if (!runtimeStore) {
    return;
  }
  const runId = state.runId || "";
  const threadId = state.threadId || "";
  const actorScope = state.actorScope || {};
  const governedAnalysisPlan = governedAnalysisAuditFromContext(state.analyticsContext);
  const runtimeEvents = (state.runtimeEvents || []).slice(Number(state.turnRuntimeEventStart || 0));
  for (const event of runtimeEvents) {
    await runtimeStore.appendRunEvent({ ...event, runId, threadId, actorScope });
  }
  for (const audit of toolAuditsFromContext(state.mainAgentToolContext, { runId, threadId, actorScope })) {
    await runtimeStore.appendToolAudit(audit);
  }
  if (typeof runtimeStore.appendRunSummary === "function") {
    await runtimeStore.appendRunSummary(buildRuntimeRunSummary({
      runId,
      threadId,
      actorScope,
      metrics: state.metrics,
      mainAgentToolContext: state.mainAgentToolContext,
      governedAnalysisPlan,
    }));
  }
  await runtimeStore.writeThreadCheckpoint({
    runId,
    threadId,
    actorScope,
    checkpoint: {
      node: "finalize",
      queryText: state.queryText || "",
      metrics: state.metrics || {},
      runtimeEvents,
      ...(governedAnalysisPlan ? { governedAnalysisPlan } : {}),
    },
  });
}

function runtimeErrorPayload(error) {
  return {
    name: String(error?.name || "Error"),
    message: String(error?.message || error || "Unknown runtime error"),
    stack: String(error?.stack || ""),
  };
}

async function persistRuntimeFailure(runtimeStore, { runId, threadId, actorScope, queryText, error, mainAgentToolContext, runtimeEvents = [], metrics = {} }) {
  if (!runtimeStore) return;
  for (const event of runtimeEvents) {
    await runtimeStore.appendRunEvent({ ...event, runId, threadId, actorScope });
  }
  for (const audit of toolAuditsFromContext(mainAgentToolContext, { runId, threadId, actorScope })) {
    await runtimeStore.appendToolAudit(audit);
  }
  await runtimeStore.appendRunEvent({
    runId,
    threadId,
    actorScope,
    queryText,
    type: "agent-runtime-failed",
    error: runtimeErrorPayload(error),
  });
  if (typeof runtimeStore.appendRunSummary === "function") {
    await runtimeStore.appendRunSummary({
      ...buildRuntimeRunSummary({ runId, threadId, actorScope, metrics, mainAgentToolContext }),
      outcome: "failed",
      failureCode: "AGENT_RUNTIME_FAILED",
    });
  }
}

export function resolveAgentRuntimeMode(env = process.env) {
  const raw = String(env?.VIZION_AGENT_RUNTIME || "").trim().toLowerCase();
  if (raw === "legacy") return "langgraph";
  return SUPPORTED_RUNTIME_MODES.has(raw) ? raw : "pi";
}

function compactToolRouting(toolRouting) {
  const selectedToolset = toolRouting?.selectedToolset || {};
  return {
    shouldUseTools: Boolean(toolRouting?.shouldUseTools),
    intent: selectedToolset.intent || "general",
    confidence: selectedToolset.confidence || 0,
    reason: selectedToolset.reason || "unknown",
    requiredSlots: Array.isArray(selectedToolset.requiredSlots) ? selectedToolset.requiredSlots : [],
    policyHints: Array.isArray(selectedToolset.policyHints) ? selectedToolset.policyHints : [],
    toolNames: Array.isArray(selectedToolset.toolNames) ? selectedToolset.toolNames : [],
  };
}

function buildMainAgentToolContextFromState(state) {
  const toolCalls = (state.toolCalls || []).slice(Number(state.turnToolCallStart || 0));
  const toolMessages = (state.toolMessages || []).slice(Number(state.turnToolMessageStart || 0));
  const toolConversationMessages = (state.toolConversationMessages || []).slice(Number(state.turnToolConversationStart || 0));
  const toolEvents = (state.toolEvents || []).slice(Number(state.turnToolEventStart || 0));
  const currentEvidence = (state.toolEvidence || []).slice(Number(state.turnToolEvidenceStart || 0));
  const hasCurrentSemanticEvidence = currentEvidence.some((item) => ["query_semantic_metrics", "query_semantic_records"].includes(item?.tool));
  const inherited = hasCurrentSemanticEvidence ? null : (state.toolEvidence || []).slice(0, Number(state.turnToolEvidenceStart || 0))
    .findLast((item) => ["query_semantic_metrics", "query_semantic_records"].includes(item?.tool)
      && item?.scope?.actorScopeHash === state.actorScope?.scopeHash && evaluateSemanticEvidence([item]).status === "pass");
  const evidence = [...(inherited ? [inherited] : []), ...currentEvidence];
  const toolResultTexts = (state.toolResultTexts || []).slice(Number(state.turnToolResultStart || 0));
  return {
    contextText: [...toolResultTexts, ...(inherited ? ["# Prior authorized semantic evidence\n" + JSON.stringify({
      toolCallId: inherited.toolCallId, tool: inherited.tool, analysisRef: inherited.analysisRef,
      summary: inherited.summary, data: inherited.data?.slice(0, 20), pagination: inherited.pagination,
      scope: inherited.scope, sourceRevision: inherited.sourceRevision,
    })] : [])].filter(Boolean).join("\n\n"),
    toolCalls,
    toolMessages,
    toolConversationMessages,
    toolEvents,
    evidence,
    inheritedEvidenceCount: inherited ? 1 : 0,
    evidenceGate: evaluateSemanticEvidence(evidence),
    selectedToolset: state.toolRouting?.selectedToolset,
    stoppedReason: state.stoppedReason || "no_tool_calls",
  };
}

function resolveDeterministicQueryPlan({ analyticsContext, actorScope, selectedToolset }) {
  const candidate = analyticsContext?.semanticPlan;
  if (!candidate || typeof candidate !== "object") return null;
  try {
    const plan = validatePlan(candidate);
    const toolNames = new Set(selectedToolset?.toolNames || []);
    if (plan.status !== "valid" || plan.actorScopeHash !== String(actorScope?.scopeHash || "")) return null;
    if (plan.executionFingerprint !== fingerprintQueryPlanSteps(plan.steps)) return null;
    if (!plan.steps.length || !plan.steps.every((step) => step.riskLevel === "R0" && toolNames.has(step.toolName))) return null;
    return plan;
  } catch {
    return null;
  }
}

function canonicalToolCalls(plan) {
  return plan.steps.map((step) => ({
    id: `${plan.planId}:${step.stepId}`,
    type: "function",
    function: {
      name: step.toolName,
      arguments: JSON.stringify(step.canonicalArgs),
    },
  }));
}

export function createGovernedChatStages({
  resolveAnalyticsContext = resolveAiAnalyticsContext,
  resolveDefectContext = resolveAiDefectContext,
  ontologyRegistry = createDefaultOntologyRegistry(),
  shouldPlanTools = shouldPlanMainAgentTools,
  requestToolCompletion = requestCompanyChatCompletion,
  requestAnswerCompletion = requestCompanyChatCompletion,
  executeToolCall,
  discoverTools = null,
  maxToolSteps = 4,
  runtimeStore = null,
  now = () => new Date(),
  runtimeKind = "langgraph",
} = {}) {
  ensureAbortSignalCompatibility();

  async function initialize(state, config) {
    const body = state.body && typeof state.body === "object" ? state.body : {};
    const runId = isNonEmptyString(state.runId) ? String(state.runId).trim() : normalizeRunId(body, now);
    const threadId = isNonEmptyString(state.threadId) ? String(state.threadId).trim() : normalizeThreadId(body, now);
    const actorScope = normalizeActorScope(body);
    const queryText = extractLatestUserQuery(body?.messages);
    const event = emit(config, {
      type: "agent-runtime-started",
      runtime: runtimeKind,
      runId,
      threadId,
      queryPreview: queryText.slice(0, 120),
    });
    return {
      body,
      runId,
      threadId,
      actorScope,
      queryText,
      model: String(body?.model || ""),
      plannedToolCalls: [],
      toolExecutionMode: "model",
      toolStepIndex: 0,
      stoppedReason: "",
      preparedCompletion: null,
      turnToolCallStart: (state.toolCalls || []).length,
      turnToolMessageStart: (state.toolMessages || []).length,
      turnToolConversationStart: (state.toolConversationMessages || []).length,
      turnToolEventStart: (state.toolEvents || []).length,
      turnToolEvidenceStart: (state.toolEvidence || []).length,
      turnToolResultStart: (state.toolResultTexts || []).length,
      turnRuntimeEventStart: (state.runtimeEvents || []).length,
      runtimeEvents: [event],
    };
  }

  async function resolveContext(state, config) {
    const body = state.body || {};
    if (classifyDirectMainAgentIntent(state.queryText || extractLatestUserQuery(body?.messages))) {
      return {
        analyticsContext: null,
        defectContext: null,
        baseContext: "",
        runtimeEvents: [],
      };
    }
    let analyticsContext = null;
    let defectContext = null;
    const events = [];

    if (body?.useAnalyticsContext === true) {
      if (!isAnalyticsActorScopeConfigured(state.actorScope)) {
        analyticsContext = {
          contextText: "# Governed data access\nStatus: BLOCKED\nReason: a server-owned workspace, team, or project scope and allowed object types are required before data access.",
          skipDefectContext: true,
        };
        events.push(emit(config, { type: "analytics-context-blocked", threadId: state.threadId }));
      } else {
        events.push(emit(config, { type: "analytics-context-started", threadId: state.threadId }));
        analyticsContext = await resolveAnalyticsContext({
          messages: body?.messages,
          actor: state.actorScope,
          ...(runtimeKind === "pi" ? { interpretQuery: false, model: body?.model } : {}),
          ...(ontologyRegistry ? { ontologyRegistry } : {}),
        });
        const governedAnalysisPlan = governedAnalysisAuditFromContext(analyticsContext);
        if (governedAnalysisPlan) {
          events.push(emit(config, {
            type: "governed-analysis-plan-ready",
            threadId: state.threadId,
            analysisPlan: governedAnalysisPlan,
          }));
        }
      }
    }

    if (runtimeKind !== "pi" && body?.useDefectContext === true && !analyticsContext?.skipDefectContext && !isOidcScopedActor(state.actorScope)) {
      events.push(emit(config, { type: "defect-context-started", threadId: state.threadId }));
      defectContext = await resolveDefectContext({
        messages: body?.messages,
        topK: 5,
        ...(state.toolDependencies || {}),
      });
    }

    const baseContext = mergeContext(body?.context, analyticsContext?.contextText, defectContext?.contextText);
    return {
      analyticsContext,
      defectContext,
      baseContext,
      runtimeEvents: events,
    };
  }

  async function routeTools(state, config) {
    const body = state.body || {};
    let selectedToolset = runtimeKind === "pi"
      ? selectModelToolset(body?.messages)
      : selectMainAgentToolset(body?.messages, state.toolCalls);
    let capabilityContext = "";
    if (typeof discoverTools === "function") {
      const readiness = await discoverTools();
      const available = new Set(readiness.availableToolNames);
      const tools = selectedToolset.tools.filter((tool) => available.has(tool.function.name));
      selectedToolset = { ...selectedToolset, tools, toolNames: tools.map((tool) => tool.function.name) };
      capabilityContext = `# Backend tool readiness\nCurrently unavailable: ${(readiness.unavailableToolNames || []).join(", ") || "none"}. Do not invoke unavailable tools or describe them as ready. Use the available governed capabilities, or explain a concrete data limitation.`;
    }
    const actorScopeConfigured = isAnalyticsActorScopeConfigured(state.actorScope);
    const needsDataTools = body?.useAnalyticsContext === true
      && (shouldPlanTools(body?.messages) || selectedToolset.policyHints?.includes("resolve_defect_reporter_before_aggregate"));
    const directResponse = classifyDirectMainAgentIntent(state.queryText || extractLatestUserQuery(body?.messages))
      || (!actorScopeConfigured && needsDataTools ? buildDataAccessUnavailableResponse() : null);
    const governedPlanDenied = state.analyticsContext?.analysisPlan?.status === "denied";
    const shouldUseTools =
      !directResponse &&
      actorScopeConfigured &&
      !governedPlanDenied &&
      body?.useAnalyticsContext === true &&
      !state.analyticsContext?.skipDefectContext &&
      (shouldPlanTools(body?.messages) || selectedToolset.policyHints?.includes("resolve_defect_reporter_before_aggregate"));
    const toolRouting = { shouldUseTools, selectedToolset };
    const compact = compactToolRouting(toolRouting);
    const event = emit(config, {
      type: "tool-routing-completed",
      threadId: state.threadId,
      shouldUseTools: compact.shouldUseTools,
      intent: directResponse?.intent || compact.intent,
      toolNames: compact.toolNames,
    });
    return {
      directResponse,
      toolRouting,
      baseContext: mergeContext(state.baseContext, capabilityContext),
      runtimeEvents: [event],
    };
  }

  function buildToolDependencies(state) {
    const toolDependencies = { ...(state.toolDependencies || {}) };
    if (hasActorScope(state.actorScope)) {
      toolDependencies.actor = state.actorScope;
    }
    if (state.analyticsContext?.queryPlan?.status === "valid") {
      toolDependencies.governedQueryPlan = state.analyticsContext.queryPlan;
    }
    return toolDependencies;
  }

  async function planToolCalls(state, config) {
    const body = state.body || {};
    const selectedToolset = state.toolRouting?.selectedToolset || selectMainAgentToolset(body?.messages);
    const events = Number(state.toolStepIndex || 0) === 0
      ? [emit(config, { type: "tool-planning-started", threadId: state.threadId })]
      : [];
    const priorToolCalls = (state.toolCalls || []).slice(Number(state.turnToolCallStart || 0));
    const priorToolMessages = (state.toolMessages || []).slice(Number(state.turnToolMessageStart || 0));
    const defectReporterLookup = runtimeKind !== "pi" && buildDefectReporterLookupToolCall(body?.messages, priorToolCalls, state.toolCalls);
    if (defectReporterLookup) {
      return {
        plannedToolCalls: [defectReporterLookup],
        plannedToolCallSource: "defect_reporter_lookup",
        toolExecutionMode: "model",
        stoppedReason: "",
        runtimeEvents: events,
      };
    }
    const defectReporterAliasLookup = runtimeKind !== "pi" && buildDefectReporterAliasLookupToolCall({
      messages: body?.messages,
      toolMessages: priorToolMessages,
      priorToolCalls,
      reporterHistory: state.toolCalls,
    });
    if (defectReporterAliasLookup) {
      return {
        plannedToolCalls: [defectReporterAliasLookup],
        plannedToolCallSource: "defect_reporter_alias_lookup",
        toolExecutionMode: "model",
        stoppedReason: "",
        runtimeEvents: events,
      };
    }
    const defectReporterAggregate = runtimeKind !== "pi" && buildDefectReporterAggregateToolCall({
      messages: body?.messages,
      toolMessages: priorToolMessages,
      priorToolCalls,
      reporterHistory: state.toolCalls,
      now: now(),
    });
    if (defectReporterAggregate) {
      return {
        plannedToolCalls: [defectReporterAggregate],
        plannedToolCallSource: "defect_reporter_aggregate",
        toolExecutionMode: "deterministic",
        stoppedReason: "",
        runtimeEvents: events,
      };
    }
    const defectReporterClarification = runtimeKind !== "pi" && buildDefectReporterClarificationToolCall({
      messages: body?.messages,
      toolMessages: priorToolMessages,
      priorToolCalls,
      reporterHistory: state.toolCalls,
    });
    if (defectReporterClarification) {
      return {
        plannedToolCalls: [defectReporterClarification],
        plannedToolCallSource: "defect_reporter_clarification",
        toolExecutionMode: "model",
        stoppedReason: "",
        runtimeEvents: events,
      };
    }
    const governedPlanToolCalls = buildReadySemanticPlanToolCalls({
      analyticsContext: state.analyticsContext,
      actorScope: state.actorScope,
      selectedToolset,
    });
    if (governedPlanToolCalls.length) {
      return {
        plannedToolCalls: governedPlanToolCalls,
        plannedToolCallSource: "governed_semantic_plan",
        toolExecutionMode: "deterministic",
        stoppedReason: "",
        runtimeEvents: events,
      };
    }
    const deterministicPlan = Number(state.toolStepIndex || 0) === 0
      ? resolveDeterministicQueryPlan({ analyticsContext: state.analyticsContext, actorScope: state.actorScope, selectedToolset })
      : null;
    if (deterministicPlan) {
      const plannedToolCalls = canonicalToolCalls(deterministicPlan);
      events.push(emit(config, {
        type: "deterministic-tool-plan-ready",
        threadId: state.threadId,
        planId: deterministicPlan.planId,
        toolNames: plannedToolCalls.map((toolCall) => toolCall.function.name),
      }));
      return {
        plannedToolCalls,
        plannedToolCallSource: "canonical_query_plan",
        toolExecutionMode: "deterministic",
        stoppedReason: "",
        runtimeEvents: events,
      };
    }
    const conversationEvidence = buildMainAgentToolContextFromState(state);
    const planningResult = await requestToolCompletion({
      messages: [
        ...(Array.isArray(body?.messages) ? body.messages : []),
        ...(state.toolConversationMessages || []).slice(Number(state.turnToolConversationStart || 0)),
      ],
      model: body?.model,
      context: mergeContext(
        state.baseContext,
        buildToolPlanningContext(now(), { modelDriven: runtimeKind === "pi" }),
        buildSelectedToolsetContext(selectedToolset),
        buildSemanticContinuationContext(state.toolEvidence || [], state.actorScope),
        conversationEvidence.contextText,
        formatSemanticEvidenceGate(conversationEvidence.evidenceGate),
        buildCitationContractContext({ evidence: conversationEvidence.evidence, registry: ontologyRegistry }),
      ),
      tools: selectedToolset.tools,
      toolChoice: "auto",
    });
    const modelToolCalls = Array.isArray(planningResult.toolCalls) ? planningResult.toolCalls.slice(0, 3) : [];
    const priorTurnCalls = (state.toolCalls || []).slice(Number(state.turnToolCallStart || 0));
    const { uniqueToolCalls: plannedToolCalls, removedDuplicate } = removeRepeatedToolCalls(modelToolCalls, priorTurnCalls);
    if (!plannedToolCalls.length) {
      return {
        plannedToolCalls: [],
        plannedToolCallSource: "",
        stoppedReason: removedDuplicate ? "duplicate_tool_call" : "no_tool_calls",
        toolExecutionMode: "model",
        preparedCompletion: !modelToolCalls.length && typeof planningResult.content === "string" && planningResult.content.trim()
          ? { content: planningResult.content, answerModel: planningResult.answerModel || state.model, ...(planningResult.usage ? { usage: planningResult.usage } : {}) }
          : null,
        runtimeEvents: events,
      };
    }
    return {
      plannedToolCalls,
      plannedToolCallSource: "model",
      toolExecutionMode: "model",
      stoppedReason: "",
      runtimeEvents: events,
    };
  }

  async function executeToolCalls(state, { deferTurnStop = false } = {}) {
    const selectedToolset = state.toolRouting?.selectedToolset;
    const toolDependencies = buildToolDependencies(state);
    const plannedToolCalls = Array.isArray(state.plannedToolCalls) ? state.plannedToolCalls : [];
    const toolCalls = [];
    const toolMessages = [];
    const toolConversationMessages = [];
    const toolEvents = [];
    const toolEvidence = [];
    const toolResultTexts = [];
    const allToolCalls = (state.toolCalls || []).slice(Number(state.turnToolCallStart || 0));
    let stoppedReason = "";

    async function executeOne(toolCall, { catalogRecovery = null, deferCatalogStop = false } = {}) {
      toolCalls.push(toolCall);
      toolConversationMessages.push({ role: "assistant", content: "", tool_calls: [toolCall] });
      const executed = await executeMainAgentPlannedToolCall({
        toolCall,
        selectedToolset,
        executeToolCall,
        toolDependencies,
        allowModelRecovery: runtimeKind === "pi",
      });
      toolMessages.push(executed.result.toolMessage);
      toolConversationMessages.push(executed.result.toolMessage);
      toolEvents.push(...executed.toolEvents);
      if (executed.evidence) {
        toolEvidence.push(executed.evidence);
      }
      if (executed.result.contextText) {
        toolResultTexts.push(executed.result.contextText);
      }
      const deferredCatalogStop = runtimeKind !== "pi" && deferCatalogStop && executed.recovery?.action === "catalog";
      if (executed.stoppedReason && !deferredCatalogStop) {
        stoppedReason = executed.stoppedReason;
      }
      if (catalogRecovery) {
        toolEvents.push({
          type: "tool-recovery",
          toolCallId: toolCall?.id || "",
          toolName: toolCall?.function?.name || "unknown_tool",
          recovery: completeCatalogBackedAnalyticsRetry({
            recovery: catalogRecovery,
            result: executed.result,
            stoppedReason: executed.stoppedReason,
          }),
        });
      }
      return {
        result: executed.result,
        recovery: executed.recovery,
        stoppedReason: executed.stoppedReason,
        deferredCatalogStop,
      };
    }

    for (const toolCall of plannedToolCalls) {
      allToolCalls.push(toolCall);
      const execution = await executeOne(toolCall, { deferCatalogStop: true });
      const result = execution.result;
      if (execution.deferredCatalogStop) {
        const correction = buildGovernedSemanticPlanRetry({
          originalToolCall: toolCall,
          recovery: execution.recovery,
          queryPlan: toolDependencies.governedQueryPlan,
          actorScopeHash: state.actorScope?.scopeHash,
        });
        if (correction && isToolAllowed(correction.toolCall.function.name, selectedToolset)) {
          allToolCalls.push(correction.toolCall);
          await executeOne(correction.toolCall, { catalogRecovery: correction.recovery });
          if (!stoppedReason) {
            stoppedReason = "catalog_retry_completed";
          }
        } else {
          stoppedReason = execution.stoppedReason || "tool_recovery_catalog";
        }
        break;
      }
      if (stoppedReason) {
        break;
      }
      if (runtimeKind !== "pi" && isCompletedTopIssueGrowthRank(toolCall, result)) {
        stoppedReason = "top_issue_growth_rank_completed";
        break;
      }
      if (runtimeKind !== "pi" && hasEmptyAnalyticsResult(toolCall, result) && !hasPlannedDiagnosis(allToolCalls)) {
        const diagnosisToolCall = buildEmptyDiagnosisToolCall(toolCall);
        allToolCalls.push(diagnosisToolCall);
        const diagnosisExecution = await executeOne(diagnosisToolCall);
        if (stoppedReason) {
          break;
        }
        const correction = buildCatalogBackedAnalyticsRetry({
          originalToolCall: toolCall,
          diagnosisToolCall,
          diagnosisResult: diagnosisExecution.result,
        });
        if (correction && isToolAllowed(correction.toolCall.function.name, selectedToolset)) {
          allToolCalls.push(correction.toolCall);
          await executeOne(correction.toolCall, { catalogRecovery: correction.recovery });
          if (!stoppedReason) {
            stoppedReason = "catalog_retry_completed";
          }
          break;
        }
      }
    }

    const nextStepIndex = Number(state.toolStepIndex || 0) + 1;
    if (!stoppedReason && !deferTurnStop && state.toolExecutionMode === "deterministic") {
      stoppedReason = "deterministic_plan_complete";
    }
    if (!stoppedReason && !deferTurnStop && nextStepIndex >= Math.max(1, Math.min(10, Number(maxToolSteps || 4)))) {
      stoppedReason = "max_steps";
    }
    return {
      plannedToolCalls: [],
      toolStepIndex: nextStepIndex,
      stoppedReason,
      toolCalls,
      toolMessages,
      toolConversationMessages,
      toolEvents,
      toolEvidence,
      toolResultTexts,
    };
  }

  async function finalize(state, config) {
    const body = state.body || {};
    const mainAgentToolContext = state.toolRouting?.shouldUseTools ? buildMainAgentToolContextFromState(state) : null;
    const citationContractContext = buildCitationContractContext({ evidence: mainAgentToolContext?.evidence, registry: ontologyRegistry });
    const context = mergeContext(
      state.baseContext,
      mainAgentToolContext?.contextText,
      formatSemanticEvidenceGate(mainAgentToolContext?.evidenceGate),
      citationContractContext,
    );
    const finalMessages = [
      ...(Array.isArray(body?.messages) ? body.messages : []),
    ];
    const prefaceEvents = [
      ...(mainAgentToolContext?.toolEvents || []),
      ...(state.defectContext?.duplicateSearchResult
        ? [
            {
              type: "context",
              context: state.defectContext.contextText,
              result: state.defectContext.duplicateSearchResult,
              timings: state.defectContext.timings,
            },
          ]
        : []),
    ];
    const metrics = {
      runtime: runtimeKind,
      runId: state.runId,
      threadId: state.threadId,
      actorId: state.actorScope?.actorId || "",
      queryText: state.queryText,
      aiContextEnabled: body?.useDefectContext === true,
      analyticsContextEnabled: body?.useAnalyticsContext === true,
      mainAgentToolCallCount: mainAgentToolContext?.toolCalls?.length || 0,
      evidenceGate: mainAgentToolContext?.evidenceGate || { status: "not_required", violations: [], analysisRefs: [], sourceRevisionIds: [], warnings: [] },
      toolRouting: state.directResponse
        ? { ...compactToolRouting(state.toolRouting), intent: state.directResponse.intent }
        : compactToolRouting(state.toolRouting),
      aiContextTimings: state.defectContext?.timings || null,
    };
    const event = emit(config, { type: "agent-runtime-ready", runId: state.runId, threadId: state.threadId });
    return {
      context,
      finalMessages,
      prefaceEvents,
      mainAgentToolContext,
      directResponse: state.directResponse || null,
      metrics,
      runtimeEvents: [event],
    };
  }

  async function completeResponse(state) {
    const mainAgentToolContext = state.toolRouting?.shouldUseTools ? buildMainAgentToolContextFromState(state) : null;
    const context = mergeContext(
      state.baseContext,
      mainAgentToolContext?.contextText,
      formatSemanticEvidenceGate(mainAgentToolContext?.evidenceGate),
      buildCitationContractContext({ evidence: mainAgentToolContext?.evidence, registry: ontologyRegistry }),
    );
    const completion = await requestAnswerCompletion({ messages: state.body?.messages || [], model: state.model, context, tools: [] });
    if (!completion || typeof completion.content !== "string" || !completion.content.trim() || completion.toolCalls?.length) {
      throw new Error("Company model did not return a completed answer");
    }
    return { preparedCompletion: { content: completion.content, answerModel: completion.answerModel || state.model, ...(completion.usage ? { usage: completion.usage } : {}) } };
  }

  function routeAfterToolRouting(state) {
    return state.toolRouting?.shouldUseTools === true ? "plan_tool_calls" : "finalize";
  }

  function routeAfterPlanning(state) {
    return Array.isArray(state.plannedToolCalls) && state.plannedToolCalls.length ? "execute_tool_calls" : "finalize";
  }

  function routeAfterToolExecution(state) {
    if (["governed_semantic_plan", "defect_reporter_aggregate"].includes(state.plannedToolCallSource)) {
      return "finalize";
    }
    return state.stoppedReason ? "finalize" : "plan_tool_calls";
  }

  return {
    initialize, resolveContext, routeTools, planToolCalls, executeToolCalls, finalize, completeResponse,
    routeAfterToolRouting, routeAfterPlanning, routeAfterToolExecution,
    prepareInvocation({ body = {}, toolDependencies = {} } = {}) {
      return { body, toolDependencies, actorScope: normalizeActorScope(body), runId: normalizeRunId(body, now), threadId: normalizeThreadId(body, now) };
    },
    persistState: (state) => persistRuntimeState(runtimeStore, state),
    persistFailure: (state, error) => persistRuntimeFailure(runtimeStore, {
      runId: state.runId, threadId: state.threadId, actorScope: normalizeActorScope(state.body),
      queryText: extractLatestUserQuery(state.body?.messages), error,
      mainAgentToolContext: state.toolRouting?.shouldUseTools ? buildMainAgentToolContextFromState(state) : null,
      runtimeEvents: state.runtimeEvents || [], metrics: state.metrics || {},
    }),
    resultFromState(state) {
      return {
        runtime: runtimeKind, runId: state.runId, threadId: state.threadId,
        actorScope: state.actorScope || {}, body: state.body || {}, queryText: state.queryText || "",
        analyticsContext: state.analyticsContext || null, aiContext: state.defectContext || null,
        mainAgentToolContext: state.mainAgentToolContext || null, directResponse: state.directResponse || null,
        context: state.context || "", finalMessages: state.finalMessages || [], prefaceEvents: state.prefaceEvents || [],
        preparedCompletion: state.preparedCompletion || null,
        events: toolLifecycleEventsFromContext(state.mainAgentToolContext, { runId: state.runId, threadId: state.threadId }),
        metrics: state.metrics || {}, runtimeEvents: (state.runtimeEvents || []).slice(Number(state.turnRuntimeEventStart || 0)),
      };
    },
  };
}
