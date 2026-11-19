import { Agent } from "@earendil-works/pi-agent-core";
import { createAssistantMessageEventStream, getCurrentSystemPrompt } from "@earendil-works/pi-ai";
export { createPiChatRuntime } from "./piChatRuntime.mjs";

import { requestCompanyChatCompletion } from "../companyChat.mjs";
import { MAIN_AGENT_TOOLS, executeMainAgentToolCall } from "../mainAgentTools.mjs";
import { buildCatalogBackedAnalyticsRetry, completeCatalogBackedAnalyticsRetry } from "../mainAgentToolRecovery.mjs";
import {
  buildEmptyDiagnosisToolCall,
  buildSelectedToolsetContext,
  buildToolPlanningContext,
  executeMainAgentPlannedToolCall,
  hasEmptyAnalyticsResult,
  hasPlannedDiagnosis,
  mergeToolContext,
  selectMainAgentToolset,
} from "../mainAgentToolPlanning.mjs";

const READ_ONLY_TOOLS = new Set([
  "get_data_catalog", "get_ontology_catalog", "search_octane_fields",
  "search_analytics_filter_values", "resolve_business_terms", "query_analytics",
  "diagnose_analytics_empty", "query_analytics_fallback", "query_semantic_metrics",
  "query_semantic_records", "query_traceability", "ask_clarification",
]);

function emptyUsage() {
  return {
    input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function textContent(content) {
  return typeof content === "string" ? content : (content || [])
    .filter((part) => part.type === "text").map((part) => part.text).join("\n");
}

function legacyCall(toolCall) {
  return {
    id: toolCall.id,
    type: "function",
    function: { name: toolCall.name, arguments: JSON.stringify(toolCall.arguments) },
  };
}

function legacyMessages(messages) {
  return messages.filter((message) => message.role !== "system").map((message) => {
    if (message.role === "toolResult") {
      return { role: "tool", tool_call_id: message.toolCallId, name: message.toolName, content: textContent(message.content) };
    }
    const toolCalls = (Array.isArray(message.content) ? message.content : [])
      .filter((part) => part.type === "toolCall").map(legacyCall);
    return {
      role: message.role,
      content: textContent(message.content),
      ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
    };
  });
}

function piCall(toolCall) {
  let args = toolCall?.function?.arguments ?? "{}";
  try {
    args = typeof args === "string" ? JSON.parse(args) : args;
    if (!args || typeof args !== "object" || Array.isArray(args)) throw new Error("Expected object arguments");
    return { type: "toolCall", id: toolCall.id, name: toolCall.function.name, arguments: args };
  } catch {
    return {
      type: "toolCall", id: toolCall.id, name: toolCall?.function?.name || "unknown_tool",
      arguments: {}, argumentsError: "Tool arguments must be a valid JSON object",
    };
  }
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function runPiMainAgentToolTurn({
  messages = [], model = "", context = "", selectedToolset: providedToolset,
  requestToolCompletion = requestCompanyChatCompletion,
  executeToolCall = executeMainAgentToolCall,
  toolDependencies = {}, maxSteps = 4, now = new Date(), runId = "", threadId = "",
} = {}) {
  if (!Array.isArray(messages) || !messages.length || messages.some((message) =>
    !["user", "assistant", "system"].includes(message.role) || typeof message.content !== "string")) {
    throw new TypeError("Pi PoC accepts nonempty text-only conversation messages");
  }
  const sourceToolset = providedToolset || selectMainAgentToolset(messages);
  const tools = MAIN_AGENT_TOOLS.filter((tool) => READ_ONLY_TOOLS.has(tool.function.name)
    && sourceToolset.toolNames?.includes(tool.function.name));
  const selectedToolset = { ...sourceToolset, tools, toolNames: tools.map((tool) => tool.function.name) };
  const requestedSteps = Number(maxSteps);
  const stepLimit = Number.isFinite(requestedSteps) ? Math.max(1, Math.min(10, Math.floor(requestedSteps))) : 4;
  const results = [];
  const toolCalls = [];
  const toolEvents = [];
  const evidence = [];
  const events = [];
  const piEvents = [];
  const recordedCalls = new Set();
  const deferredCalls = new Set();
  const plannedOriginalCalls = new Map();
  const seenSignatures = new Set();
  const automaticCalls = [];
  const automaticContexts = new Map();
  let modelCalls = 0;
  let stoppedReason = "";
  const event = (item) => events.push({ runId, threadId, ...item });
  event({ type: "agent.turn.started", intent: selectedToolset.intent, toolNames: selectedToolset.toolNames });

  function record(executed) {
    results.push(executed.result);
    recordedCalls.add(executed.result.toolMessage.tool_call_id);
    toolEvents.push(...executed.toolEvents);
    if (executed.evidence) evidence.push(executed.evidence);
    if (executed.stoppedReason && !stoppedReason) stoppedReason = executed.stoppedReason;
  }

  const agent = new Agent({
    initialState: {
      systemPrompt: mergeToolContext(context, buildToolPlanningContext(now), buildSelectedToolsetContext(selectedToolset)),
      model: {
        id: model || "company-default", name: "Vizion company transport", provider: "vizion-company",
        api: "openai-completions", baseUrl: "", input: ["text"], reasoning: false,
        contextWindow: 32768, maxTokens: 4096, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
      tools: tools.map((tool) => ({
        name: tool.function.name, label: tool.function.name,
        description: tool.function.description, parameters: tool.function.parameters,
        executionMode: "sequential",
        execute: async (toolCallId, args) => {
          const toolCall = { id: toolCallId, type: "function", function: { name: tool.function.name, arguments: JSON.stringify(args) } };
          const executed = await executeMainAgentPlannedToolCall({
            toolCall,
            selectedToolset, executeToolCall, toolDependencies,
          });
          record(executed);
          const automatic = automaticContexts.get(toolCallId);
          if (automatic?.catalogRecovery) {
            toolEvents.push({
              type: "tool-recovery", toolCallId, toolName: tool.function.name,
              recovery: completeCatalogBackedAnalyticsRetry({
                recovery: automatic.catalogRecovery, result: executed.result, stoppedReason: executed.stoppedReason,
              }),
            });
            stoppedReason ||= "catalog_retry_completed";
          } else if (!stoppedReason && automatic?.originalToolCall) {
            const correction = buildCatalogBackedAnalyticsRetry({
              originalToolCall: automatic.originalToolCall, diagnosisToolCall: toolCall, diagnosisResult: executed.result,
            });
            if (correction && selectedToolset.toolNames.includes(correction.toolCall.function.name)) {
              automaticCalls.push({ toolCall: correction.toolCall, catalogRecovery: correction.recovery });
            }
          } else if (!stoppedReason && hasEmptyAnalyticsResult(toolCall, executed.result)
            && !hasPlannedDiagnosis(toolCalls) && selectedToolset.toolNames.includes("diagnose_analytics_empty")) {
            automaticCalls.push({ toolCall: buildEmptyDiagnosisToolCall(toolCall), originalToolCall: toolCall });
          }
          return {
            content: [{ type: "text", text: executed.result.toolMessage.content }],
            details: {}, isError: executed.blocked, terminate: Boolean(stoppedReason),
          };
        },
      })),
    },
    toolExecution: "sequential",
    beforeToolCall: async ({ toolCall, args }) => {
      if (stoppedReason) return { block: true, reason: "turn_stopped", terminate: true };
      if (automaticCalls.length) return { block: true, reason: "recovery_pending" };
      const signature = `${toolCall.name}:${canonicalJson(args)}`;
      if (seenSignatures.has(signature)) {
        stoppedReason = "repeated_tool_calls";
        return { block: true, reason: stoppedReason, terminate: true };
      }
      seenSignatures.add(signature);
      return undefined;
    },
    streamFn: (piModel, transcript) => {
      const stream = createAssistantMessageEventStream();
      const assistant = {
        role: "assistant", content: [], api: piModel.api, provider: piModel.provider,
        model: piModel.id, timestamp: Date.now(), usage: emptyUsage(), stopReason: "pending",
      };
      void (async () => {
        try {
          const automatic = automaticCalls.shift();
          let completion;
          if (automatic) {
            automaticContexts.set(automatic.toolCall.id, automatic);
            completion = { content: "", toolCalls: [automatic.toolCall] };
          } else {
            modelCalls += 1;
            completion = await requestToolCompletion({
              messages: legacyMessages(transcript.messages), model,
              context: getCurrentSystemPrompt(transcript.messages), tools: selectedToolset.tools, toolChoice: "auto",
            });
          }
          stream.push({ type: "start", partial: assistant });
          const plannedCalls = (Array.isArray(completion.toolCalls) ? completion.toolCalls : []).slice(0, 3);
          for (const call of plannedCalls) plannedOriginalCalls.set(call.id, call);
          assistant.content = [
            ...(completion.content ? [{ type: "text", text: completion.content }] : []),
            ...plannedCalls.map(piCall),
          ];
          assistant.stopReason = plannedCalls.length ? "toolUse" : "stop";
          stream.push({ type: "done", reason: assistant.stopReason, message: assistant });
        } catch {
          assistant.stopReason = "error";
          assistant.errorMessage = "Company model planning failed";
          stream.push({ type: "error", reason: "error", error: assistant });
        } finally {
          stream.end(assistant);
        }
      })();
      return stream;
    },
    finishTurn: ({ message }) => {
      if (message.stopReason === "error") stoppedReason ||= "model_error";
      const hasCalls = message.content.some((part) => part.type === "toolCall");
      if (!hasCalls) stoppedReason ||= "no_tool_calls";
      if (hasCalls && !automaticCalls.length && modelCalls >= stepLimit) stoppedReason ||= "max_steps";
      return stoppedReason ? { action: "end" } : undefined;
    },
  });
  agent.subscribe((item) => {
    piEvents.push({ type: item.type, ...(item.toolCallId ? { toolCallId: item.toolCallId, toolName: item.toolName } : {}) });
    if (item.type === "tool_execution_start") {
      if (stoppedReason || automaticCalls.length) {
        deferredCalls.add(item.toolCallId);
        return;
      }
      const call = plannedOriginalCalls.get(item.toolCallId)
        || legacyCall({ id: item.toolCallId, name: item.toolName, arguments: item.args });
      toolCalls.push(call);
      event({ type: "agent.tool.planned", toolCallId: item.toolCallId, toolName: item.toolName, intent: selectedToolset.intent });
      event({ type: "agent.tool.started", toolCallId: item.toolCallId, toolName: item.toolName, intent: selectedToolset.intent });
    }
    if (item.type === "tool_execution_end") {
      if (deferredCalls.has(item.toolCallId)) return;
      if (!recordedCalls.has(item.toolCallId)) {
        const reason = stoppedReason || (selectedToolset.toolNames.includes(item.toolName) ? "invalid_tool_arguments" : "tool_not_allowed");
        stoppedReason ||= reason;
        record({
          result: {
            contextText: `# Main agent tool result\nTool: ${item.toolName}\nResult: blocked: ${reason}`,
            toolMessage: { role: "tool", tool_call_id: item.toolCallId, name: item.toolName, content: JSON.stringify({ ok: false, error: reason }) },
          },
          toolEvents: [{ type: "tool-blocked", toolCallId: item.toolCallId, toolName: item.toolName, reason }],
        });
      }
      event({ type: item.isError ? "agent.tool.blocked" : "agent.tool.completed", toolCallId: item.toolCallId, toolName: item.toolName, intent: selectedToolset.intent });
    }
  });
  await agent.prompt(messages.map((message) => ({ ...message, timestamp: Date.now() })));
  if (agent.state.errorMessage) stoppedReason ||= "model_error";
  event({ type: "agent.turn.completed", intent: selectedToolset.intent, stoppedReason, toolCallCount: toolCalls.length });
  const toolConversationMessages = legacyMessages(agent.state.messages).slice(messages.filter((message) => message.role !== "system").length);
  return {
    runtime: "pi", contextText: results.map((result) => result.contextText).filter(Boolean).join("\n\n"),
    toolCalls, toolMessages: results.map((result) => result.toolMessage), toolConversationMessages,
    toolEvents, uiToolEvents: toolEvents, events, evidence, selectedToolset, stoppedReason, piEvents,
    metrics: { modelCalls, maxSteps: stepLimit, tokenUsage: null },
  };
}