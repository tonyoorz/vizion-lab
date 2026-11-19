import { extractLatestUserQuery } from "./aiContext.mjs";
import { classifyDirectMainAgentIntent } from "./mainAgentDirectIntent.mjs";
import { MAIN_AGENT_TOOLS } from "./mainAgentTools.mjs";

/**
 * Tool registry — model-driven tool selection.
 *
 * The regex-based intent cascade and per-intent tool allowlists have been
 * removed.  The model now receives the full governed tool set and chooses
 * which tools to call.  Governance (schema enums, additionalProperties:
 * false, actor-scope checks, evidence gates) applies at the execution layer.
 *
 * Direct intents (chitchat / out_of_scope / clarification) are still
 * classified so the runtime can short-circuit to a direct response.
 */

const GENERAL_PROFILE = Object.freeze({
  confidence: 0.5,
  reason: "model-driven tool selection",
  requiredSlots: [],
  policyHints: ["use_tool_policy"],
});

function toolName(toolCall) {
  return toolCall?.function?.name || "unknown_tool";
}

function toolsByName(allTools) {
  return new Map((Array.isArray(allTools) ? allTools : []).map((tool) => [tool.function?.name, tool]));
}

export function createBackendToolDiscovery({ analyticsFetch = globalThis.fetch, analyticsApiBase = process.env.VIZION_ANALYTICS_API_BASE || "http://127.0.0.1:3003" } = {}) {
  const operations = {
    get_data_catalog: [], resolve_business_terms: [], ask_clarification: [], search_duplicates: [],
    get_ontology_catalog: [["/api/ontology/catalog", "get"]],
    search_octane_fields: [["/api/ontology/fields/search", "post"]],
    search_analytics_filter_values: [["/api/analytics/filter-values/search", "post"]],
    query_analytics: [["/api/agent/analytics/defects/aggregate", "post"]],
    diagnose_analytics_empty: [["/api/agent/analytics/defects/aggregate", "post"]],
    query_analytics_fallback: [["/api/analytics/fallback/query", "post"]],
    query_semantic_metrics: [["/api/semantic/query", "post"]],
    query_semantic_records: [["/api/semantic/records", "post"]],
    query_traceability: [["/api/semantic/query", "post"]],
    query_dashboard_summary: [["/api/full-picture/dashboard/summary", "get"]],
    query_testing_coverage_project_status: [["/api/testing/coverage-analysis/project-status", "get"]],
    query_testing_coverage_aida_status: [["/api/testing/coverage-analysis/aida-status", "get"]],
    query_testing_team_fv_analysis: [["/api/agent/analytics/testing/team-fv-analysis", "post"]],
    get_test_case_context: [["/api/ontology/context", "post"]],
    query_defect_high_frequency_analysis: [["/api/full-picture/defect-high-frequency-analysis", "get"]],
    query_defect_aggregate: [["/api/agent/analytics/defects/aggregate", "post"]],
    query_defect_records: [["/api/agent/analytics/defects/records", "post"]],
    export_defect_records: [["/api/agent/analytics/defects/export", "post"]],
    query_full_picture_module: ["dashboard/summary", "dashboard/tickets", "top-issue-analysis", "long-runner-analysis", "defect-high-frequency-analysis"]
      .map((path) => [`/api/full-picture/${path}`, "get"]),
  };
  let cached = null;
  let expiresAt = 0;
  return async () => {
    if (cached && Date.now() < expiresAt) return structuredClone(cached);
    let paths = {};
    try {
      const response = await analyticsFetch(new URL("/openapi.json", analyticsApiBase).toString(), { signal: AbortSignal.timeout(3000) });
      if (response.ok) paths = (await response.json()).paths || {};
    } catch { paths = {}; }
    const availableToolNames = MAIN_AGENT_TOOLS.map((tool) => tool.function.name)
      .filter((name) => operations[name]?.every(([path, method]) => paths[path]?.[method] !== undefined));
    cached = { availableToolNames, unavailableToolNames: MAIN_AGENT_TOOLS.map((tool) => tool.function.name).filter((name) => !availableToolNames.includes(name)) };
    expiresAt = Date.now() + 30000;
    return structuredClone(cached);
  };
}

export function createMainAgentToolRegistry(allTools = MAIN_AGENT_TOOLS) {
  const toolMap = toolsByName(allTools);

  function routeIntent(messages) {
    const queryText = extractLatestUserQuery(messages);
    const directIntent = classifyDirectMainAgentIntent(queryText);
    if (directIntent) {
      return { queryText, ...directIntent, toolNames: [] };
    }
    return { intent: "general", queryText, ...GENERAL_PROFILE };
  }

  function selectToolset(messages) {
    const routed = routeIntent(messages);
    // Direct intents (chitchat / out_of_scope / clarification) get no tools.
    const directIntents = new Set(["chitchat", "out_of_scope", "clarification", "data_access_unavailable"]);
    if (directIntents.has(routed.intent)) {
      return { ...routed, toolNames: [], tools: [] };
    }
    // Return the full governed tool set — model decides which to call.
    const tools = Array.isArray(allTools) ? allTools : [];
    const toolNames = tools.map((tool) => tool.function?.name).filter(Boolean);
    return { ...routed, toolNames, tools };
  }

  function shouldPlanTools(messages) {
    return Boolean(extractLatestUserQuery(messages));
  }

  function isToolAllowed(name, selectedToolset) {
    return Array.isArray(selectedToolset?.toolNames) && selectedToolset.toolNames.includes(name);
  }

  function validateToolCall(toolCall, selectedToolset) {
    const name = toolName(toolCall);
    if (isToolAllowed(name, selectedToolset)) return { allowed: true, toolName: name };
    return {
      allowed: false,
      toolName: name,
      reason: `Tool ${name} is not in the governed tool set`,
    };
  }

  return {
    routeIntent,
    selectToolset,
    shouldPlanTools,
    isToolAllowed,
    validateToolCall,
  };
}

export function routeMainAgentIntent(messages) {
  return createMainAgentToolRegistry().routeIntent(messages);
}

export function selectMainAgentToolset(messages, allTools = MAIN_AGENT_TOOLS) {
  return createMainAgentToolRegistry(allTools).selectToolset(messages);
}

export function shouldPlanMainAgentTools(messages) {
  return createMainAgentToolRegistry().shouldPlanTools(messages);
}

export function isToolAllowed(name, selectedToolset) {
  return createMainAgentToolRegistry().isToolAllowed(name, selectedToolset);
}

export function validateToolCallAllowed(toolCall, selectedToolset) {
  return createMainAgentToolRegistry().validateToolCall(toolCall, selectedToolset);
}

export function buildBlockedToolResult(toolCall, selectedToolset, reason) {
  const name = toolName(toolCall);
  const content = JSON.stringify({ ok: false, tool: name, error: reason, intent: selectedToolset?.intent || "unknown" });
  return {
    toolMessage: {
      role: "tool",
      tool_call_id: toolCall?.id || "",
      name,
      content,
    },
    contextText: `# Main agent tool result\nTool: ${name}\nResult: blocked: ${reason}`,
  };
}
