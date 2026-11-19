import { Agent } from "@earendil-works/pi-agent-core";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai";
import { MAIN_AGENT_TOOLS } from "../mainAgentTools.mjs";
import { buildBlockedToolResult } from "../mainAgentToolRegistry.mjs";
import { buildToolEvidence } from "../mainAgentEvidence.mjs";
import { createGovernedChatStages, wrapScopedChatRuntime } from "./governedChatRuntime.mjs";

const APPEND_FIELDS = new Set([
  "toolCalls", "toolMessages", "toolConversationMessages", "toolEvents", "toolEvidence", "toolResultTexts", "runtimeEvents",
]);

function mergeState(state, update) {
  for (const [key, value] of Object.entries(update || {})) {
    state[key] = APPEND_FIELDS.has(key) ? [...(state[key] || []), ...(value || [])] : value;
  }
}

function decodedCall(call) {
  try {
    const args = typeof call.function.arguments === "string" ? JSON.parse(call.function.arguments) : call.function.arguments;
    if (!args || typeof args !== "object" || Array.isArray(args)) throw new TypeError("Invalid arguments");
    return { type: "toolCall", id: call.id, name: call.function.name, arguments: args };
  } catch {
    return { type: "toolCall", id: call.id, name: call.function.name, arguments: {}, argumentsError: "Invalid JSON object arguments" };
  }
}

function assistantMessage(model) {
  return {
    role: "assistant", content: [], api: "openai-completions", provider: "vizion-company", model: model.id,
    timestamp: Date.now(), stopReason: "pending",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
  };
}

export function createPiChatRuntime(options = {}) {
  const stages = createGovernedChatStages({ ...options, runtimeKind: "pi" });
  const requestedSteps = Number(options.maxToolSteps ?? 8);
  const maxSteps = Number.isFinite(requestedSteps) ? Math.max(1, Math.min(10, Math.floor(requestedSteps))) : 8;
  const histories = new Map();
  const threadKey = (initial) => JSON.stringify([initial.threadId, initial.actorScope]);
  const runtime = {
    kind: "pi",
    async invoke(input, { onEvent, initial }) {
      const key = threadKey(initial);
      const state = { ...structuredClone(histories.get(key) || {}), ...initial, runtimeEvents: [] };
      const config = { configurable: { onEvent } };
      const piEvents = [];
      let planningError;
      let plannedCalls = [];
      let batchStep = 0;
      const completedCalls = new Set();
      const usedCallIds = new Set();
      try {
        mergeState(state, await stages.initialize(state, config));
        mergeState(state, await stages.resolveContext(state, config));
        mergeState(state, await stages.routeTools(state, config));
        if (state.toolRouting?.shouldUseTools) {
          const selectedToolset = state.toolRouting.selectedToolset;
          const agent = new Agent({
            initialState: {
              systemPrompt: "Execute only the server-governed Chat plan and registered business tools.",
              model: { id: state.model || "company-default", name: "Company transport", api: "openai-completions",
                provider: "vizion-company", baseUrl: "", input: ["text"], reasoning: false,
                contextWindow: 32768, maxTokens: 4096, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } },
              tools: MAIN_AGENT_TOOLS.filter((tool) => selectedToolset.toolNames.includes(tool.function.name)).map((tool) => ({
                name: tool.function.name, label: tool.function.name, description: tool.function.description,
                parameters: tool.function.parameters, executionMode: "sequential",
                execute: async (id, args) => {
                  const call = { id, type: "function", function: { name: tool.function.name, arguments: JSON.stringify(args) } };
                  const update = await stages.executeToolCalls({
                    ...state, plannedToolCalls: [call], toolStepIndex: batchStep,
                  }, { deferTurnStop: true });
                  mergeState(state, update);
                  for (const executedCall of update.toolCalls || []) {
                    usedCallIds.add(executedCall.id);
                    completedCalls.add(executedCall.id);
                  }
                  completedCalls.add(id);
                  const result = update.toolMessages.find((message) => message.tool_call_id === id);
                  return { content: [{ type: "text", text: result?.content || "{}" }], details: {} };
                },
              })),
            },
            toolExecution: "sequential",
            beforeToolCall: async ({ toolCall }) => {
              if (completedCalls.has(toolCall.id)) state.stoppedReason ||= "duplicate_tool_call_id";
              return state.stoppedReason ? { block: true, reason: state.stoppedReason, terminate: true } : undefined;
            },
            streamFn: (model) => {
              const stream = createAssistantMessageEventStream();
              const message = assistantMessage(model);
              void (async () => {
                try {
                  batchStep = Number(state.toolStepIndex || 0);
                  if (state.stoppedReason && !state.preparedCompletion) {
                    mergeState(state, await stages.completeResponse(state));
                    plannedCalls = [];
                  } else {
                    mergeState(state, await stages.planToolCalls(state, config));
                    plannedCalls = state.plannedToolCalls || [];
                  }
                  const ids = plannedCalls.map((call) => call?.id);
                  if (ids.some((id) => typeof id !== "string" || !id.trim() || usedCallIds.has(id))
                    || new Set(ids).size !== ids.length) {
                    throw new Error("Invalid tool call identifiers");
                  }
                  for (const id of ids) usedCallIds.add(id);
                  message.content = [
                    ...(state.preparedCompletion ? [{ type: "text", text: state.preparedCompletion.content }] : []),
                    ...plannedCalls.map(decodedCall),
                  ];
                  message.stopReason = plannedCalls.length ? "toolUse" : "stop";
                  stream.push({ type: "start", partial: message });
                  stream.push({ type: "done", reason: message.stopReason, message });
                } catch (error) {
                  planningError = error;
                  message.stopReason = "error";
                  message.errorMessage = "Company Chat planning failed";
                  stream.push({ type: "error", reason: "error", error: message });
                } finally {
                  stream.end(message);
                }
              })();
              return stream;
            },
            finishTurn: () => {
              if (planningError || state.preparedCompletion) return { action: "end" };
              if (state.toolExecutionMode === "deterministic" && !state.stoppedReason) state.stoppedReason = "deterministic_plan_complete";
              if (state.toolStepIndex >= maxSteps && !state.stoppedReason) state.stoppedReason = "max_steps";
              return state.stoppedReason || !plannedCalls.length ? { action: "continue" } : undefined;
            },
          });
          agent.subscribe((event) => {
            const summary = { type: event.type, ...(event.toolCallId ? { toolCallId: event.toolCallId, toolName: event.toolName } : {}) };
            piEvents.push(summary);
            const runtimeEvent = { type: "pi-agent-event", runtime: "pi", runId: state.runId, threadId: state.threadId, event: summary };
            state.runtimeEvents.push(runtimeEvent);
            onEvent?.(structuredClone(runtimeEvent));
            if (event.type === "tool_execution_end" && event.isError && !completedCalls.has(event.toolCallId) && !state.stoppedReason) {
              const reason = selectedToolset.toolNames.includes(event.toolName) ? "invalid_tool_arguments" : "tool_not_allowed";
              const call = plannedCalls.find((item) => item.id === event.toolCallId);
              const blocked = buildBlockedToolResult(call, selectedToolset, reason);
              completedCalls.add(event.toolCallId);
              state.toolStepIndex = Math.max(Number(state.toolStepIndex || 0), batchStep + 1);
              mergeState(state, {
                toolCalls: [call], toolMessages: [blocked.toolMessage], toolResultTexts: [blocked.contextText],
                toolConversationMessages: [{ role: "assistant", content: "", tool_calls: [call] }, blocked.toolMessage],
                toolEvidence: [buildToolEvidence({ toolCall: call, result: blocked, intent: selectedToolset.intent })],
                toolEvents: [{ type: "tool-blocked", toolCallId: event.toolCallId, toolName: event.toolName, reason }],
              });
            }
          });
          await agent.prompt(state.queryText || "Process the governed Chat request.");
          if (planningError) throw planningError;
          if (agent.state.errorMessage) throw new Error("Pi Chat execution failed");
        } else if (!state.directResponse) {
          mergeState(state, await stages.completeResponse(state));
        }
        mergeState(state, await stages.finalize(state, config));
        await stages.persistState(state);
        const history = Object.fromEntries([...APPEND_FIELDS].filter((field) => field !== "runtimeEvents")
          .map((field) => [field, (state[field] || []).slice(-64)]));
        histories.delete(key);
        histories.set(key, structuredClone(history));
        if (histories.size > 128) histories.delete(histories.keys().next().value);
        return { ...stages.resultFromState(state), piEvents };
      } catch (error) {
        await stages.persistFailure(state, error);
        throw error;
      }
    },
  };
  return wrapScopedChatRuntime({ kind: "pi", prepareInvocation: stages.prepareInvocation, invoke: runtime.invoke });
}