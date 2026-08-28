import { requestCompanyChatCompletion } from "./companyChat.mjs";
import { executeMainAgentToolCall } from "./mainAgentTools.mjs";
import { isToolAllowed } from "./mainAgentToolsets.mjs";
import {
  buildEmptyDiagnosisToolCall,
  buildToolPlanningContext,
  executeMainAgentPlannedToolCall,
  hasEmptyAnalyticsResult,
  hasPlannedDiagnosis,
  mergeToolContext,
  selectMainAgentToolset,
} from "./mainAgentToolPlanning.mjs";

export async function resolveMainAgentToolContext({
  messages,
  model,
  context,
  requestChatCompletion = requestCompanyChatCompletion,
  executeToolCall = executeMainAgentToolCall,
  toolDependencies = {},
  selectedToolset: providedToolset,
  maxSteps = 8,
  now = new Date(),
} = {}) {
  const allToolCalls = [];
  const results = [];
  const toolConversationMessages = [];
  const toolEvents = [];
  const evidence = [];
  const stepLimit = Math.max(1, Math.min(10, Number(maxSteps || 4)));
  const selectedToolset = providedToolset || selectMainAgentToolset(messages);
  let stoppedReason = "no_tool_calls";

  async function executeAndRecordToolCall(toolCall) {
    const executed = await executeMainAgentPlannedToolCall({
      toolCall,
      selectedToolset,
      executeToolCall,
      toolDependencies,
    });
    results.push(executed.result);
    toolConversationMessages.push(executed.result.toolMessage);
    toolEvents.push(...executed.toolEvents);
    if (executed.evidence) {
      evidence.push(executed.evidence);
    }
    if (executed.stoppedReason) {
      stoppedReason = executed.stoppedReason;
    }
    return { result: executed.result, blocked: executed.blocked };
  }

  for (let stepIndex = 0; stepIndex < stepLimit; stepIndex += 1) {
    const planningResult = await requestChatCompletion({
      messages: [
        ...(Array.isArray(messages) ? messages : []),
        ...toolConversationMessages,
      ],
      model,
      context: mergeToolContext(
        context,
        buildToolPlanningContext(now),
        `# Selected toolset\nIntent: ${selectedToolset.intent}. Tools: ${selectedToolset.tools.map((tool) => tool.function?.name).join(", ")}.`,
      ),
      tools: selectedToolset.tools,
      toolChoice: "auto",
    });

    const stepToolCalls = Array.isArray(planningResult.toolCalls) ? planningResult.toolCalls.slice(0, 3) : [];
    if (stepToolCalls.length === 0) {
      stoppedReason = "no_tool_calls";
      break;
    }

    allToolCalls.push(...stepToolCalls);
    toolConversationMessages.push({
      role: "assistant",
      content: "",
      tool_calls: stepToolCalls,
    });

    for (const toolCall of stepToolCalls) {
      const { result } = await executeAndRecordToolCall(toolCall);
      if (stoppedReason === "tool_not_allowed" || stoppedReason === "clarification_requested") {
        break;
      }

      if (hasEmptyAnalyticsResult(toolCall, result) && !hasPlannedDiagnosis(allToolCalls) && isToolAllowed("diagnose_analytics_empty", selectedToolset)) {
        const diagnosisToolCall = buildEmptyDiagnosisToolCall(toolCall);
        allToolCalls.push(diagnosisToolCall);
        toolConversationMessages.push({
          role: "assistant",
          content: "",
          tool_calls: [diagnosisToolCall],
        });
        await executeAndRecordToolCall(diagnosisToolCall);
        if (stoppedReason === "tool_not_allowed" || stoppedReason === "clarification_requested") {
          break;
        }
      }
    }

    if (stoppedReason === "clarification_requested") {
      break;
    }

    if (stoppedReason === "tool_not_allowed") {
      break;
    }

    if (stepIndex === stepLimit - 1) {
      stoppedReason = "max_steps";
    }
  }

  return {
    contextText: results.map((result) => result.contextText).filter(Boolean).join("\n\n"),
    toolCalls: allToolCalls,
    toolMessages: results.map((result) => result.toolMessage),
    toolConversationMessages,
    toolEvents,
    evidence,
    selectedToolset,
    stoppedReason,
  };
}