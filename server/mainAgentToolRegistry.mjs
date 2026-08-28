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

const PLANNING_QUERY_RE = /\b(DTSV|QGate|Octane|ticket|work_item|dashboard|ontology|capability|available|partial|unavailable|bug|defect|issue|top\s*issue|octane_defects|solution_cluster|assigned_ecu|business_module|opened|created|raised|submitted|resolved|coverage|test|summary|count|metric|trend|growth|rising|increase|delta|duplicate|similar|write|update|delete|edit|risk|health|overview|assessment|empty\s*result|no\s*data|zero\s*rows|jira|atlassian)\b|[A-Z][A-Z0-9]+-\d+|entityType=work_item|id=\d+|本体|能力|缺陷|测试|测试小组|执行效率|缺陷发现率|覆盖率|多少|几个|统计|趋势|风险|健康度|当前情况|怎么看|怎么样|创建|提交|新建|解决|关闭|更新|修改|删除|写入|重复|查重|相似|模块|问题模块|上升|增长|环比|同比|根因|提票|报票|提了|数据.*(?:为空|没数据|没有数据|查不到)|为什么.*(?:为空|没数据|没有数据|查不到)|空结果/i;

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
    const queryText = extractLatestUserQuery(messages);
    if (classifyDirectMainAgentIntent(queryText)) return false;
    // Broad trigger: let the model attempt tools for any analytics-shaped query.
    return PLANNING_QUERY_RE.test(queryText);
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
