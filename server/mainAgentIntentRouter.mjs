import { extractLatestUserQuery } from "./aiContext.mjs";
import { classifyDirectMainAgentIntent } from "./mainAgentDirectIntent.mjs";

/**
 * Intent router — model-driven tool selection.
 *
 * The regex-based intent cascade and per-intent tool allowlists have been
 * removed.  The model now receives the full governed tool set and chooses
 * which tools to call on its own.  Governance (schema enums,
 * additionalProperties: false, actor-scope checks, evidence gates) still
 * applies at the execution layer.
 *
 * Direct intents (chitchat / out_of_scope / clarification) are still
 * classified here so the runtime can short-circuit to a direct response
 * without entering the tool loop.
 */

const GENERAL_PROFILE = Object.freeze({
  confidence: 0.5,
  reason: "model-driven tool selection",
  requiredSlots: [],
  policyHints: ["use_tool_policy"],
});

export function routeMainAgentIntent(messages) {
  const queryText = extractLatestUserQuery(messages);
  const directIntent = classifyDirectMainAgentIntent(queryText);
  if (directIntent) {
    return { queryText, ...directIntent };
  }
  return { intent: "general", queryText, ...GENERAL_PROFILE };
}

/**
 * Returns the full governed tool set for any non-direct query.
 * The model decides which tools to invoke; policy gate + schema enums
 * enforce governance at execution time.
 */
export function selectMainAgentToolset(messages, allTools) {
  const routed = routeMainAgentIntent(messages);
  // Direct intents (chitchat / out_of_scope / clarification) get no tools.
  const directIntents = new Set(["chitchat", "out_of_scope", "clarification", "data_access_unavailable"]);
  if (directIntents.has(routed.intent)) {
    return { ...routed, toolNames: [], tools: [] };
  }
  const tools = Array.isArray(allTools) ? allTools : [];
  return { ...routed, toolNames: tools.map((tool) => tool.function?.name).filter(Boolean), tools };
}
