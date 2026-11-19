import { createGovernedChatStages, wrapScopedChatRuntime } from "./governedChatRuntime.mjs";
export { createGovernedChatStages, resolveAgentRuntimeMode } from "./governedChatRuntime.mjs";
import { Annotation, END, MemorySaver, START, StateGraph } from "@langchain/langgraph";

const overwrite = (_left, right) => right;
const append = (left, right) => [...(Array.isArray(left) ? left : []), ...(Array.isArray(right) ? right : [])];

const ChatState = Annotation.Root({
  body: Annotation({ reducer: overwrite, default: () => ({}) }),
  toolDependencies: Annotation({ reducer: overwrite, default: () => ({}) }),
  runId: Annotation({ reducer: overwrite, default: () => "" }),
  threadId: Annotation({ reducer: overwrite, default: () => "" }),
  actorScope: Annotation({ reducer: overwrite, default: () => ({}) }),
  queryText: Annotation({ reducer: overwrite, default: () => "" }),
  model: Annotation({ reducer: overwrite, default: () => "" }),
  analyticsContext: Annotation({ reducer: overwrite, default: () => null }),
  defectContext: Annotation({ reducer: overwrite, default: () => null }),
  toolRouting: Annotation({ reducer: overwrite, default: () => null }),
  plannedToolCalls: Annotation({ reducer: overwrite, default: () => [] }),
  plannedToolCallSource: Annotation({ reducer: overwrite, default: () => "" }),
  toolExecutionMode: Annotation({ reducer: overwrite, default: () => "model" }),
  toolStepIndex: Annotation({ reducer: overwrite, default: () => 0 }),
  stoppedReason: Annotation({ reducer: overwrite, default: () => "" }),
  preparedCompletion: Annotation({ reducer: overwrite, default: () => null }),
  directResponse: Annotation({ reducer: overwrite, default: () => null }),
  toolCalls: Annotation({ reducer: append, default: () => [] }),
  toolMessages: Annotation({ reducer: append, default: () => [] }),
  toolConversationMessages: Annotation({ reducer: append, default: () => [] }),
  toolEvents: Annotation({ reducer: append, default: () => [] }),
  toolEvidence: Annotation({ reducer: append, default: () => [] }),
  toolResultTexts: Annotation({ reducer: append, default: () => [] }),
  turnToolCallStart: Annotation({ reducer: overwrite, default: () => 0 }),
  turnToolMessageStart: Annotation({ reducer: overwrite, default: () => 0 }),
  turnToolConversationStart: Annotation({ reducer: overwrite, default: () => 0 }),
  turnToolEventStart: Annotation({ reducer: overwrite, default: () => 0 }),
  turnToolEvidenceStart: Annotation({ reducer: overwrite, default: () => 0 }),
  turnToolResultStart: Annotation({ reducer: overwrite, default: () => 0 }),
  turnRuntimeEventStart: Annotation({ reducer: overwrite, default: () => 0 }),
  mainAgentToolContext: Annotation({ reducer: overwrite, default: () => null }),
  baseContext: Annotation({ reducer: overwrite, default: () => "" }),
  context: Annotation({ reducer: overwrite, default: () => "" }),
  finalMessages: Annotation({ reducer: overwrite, default: () => [] }),
  prefaceEvents: Annotation({ reducer: overwrite, default: () => [] }),
  metrics: Annotation({ reducer: overwrite, default: () => ({}) }),
  runtimeEvents: Annotation({ reducer: append, default: () => [] }),
});

function createRunnableSignal() {
  if (typeof AbortController === "undefined") {
    return undefined;
  }
  const controller = new AbortController();
  if (typeof controller.signal.throwIfAborted !== "function") {
    Object.defineProperty(controller.signal, "throwIfAborted", {
      value() {
        if (this.aborted) {
          throw new Error("LangGraph run aborted");
        }
      },
    });
  }
  return controller.signal;
}

export function createLangGraphChatRuntime(options = {}) {
  const stages = createGovernedChatStages(options);
  const {
    initialize, resolveContext, routeTools, planToolCalls, executeToolCalls, finalize,
    routeAfterToolRouting, routeAfterPlanning, routeAfterToolExecution,
  } = stages;
  const checkpointer = options.checkpointer || new MemorySaver();
  const graph = new StateGraph(ChatState)
    .addNode("initialize", initialize)
    .addNode("resolve_context", resolveContext)
    .addNode("route_tools", routeTools)
    .addNode("plan_tool_calls", planToolCalls)
    .addNode("execute_tool_calls", executeToolCalls)
    .addNode("finalize", finalize)
    .addEdge(START, "initialize")
    .addEdge("initialize", "resolve_context")
    .addEdge("resolve_context", "route_tools")
    .addConditionalEdges("route_tools", routeAfterToolRouting)
    .addConditionalEdges("plan_tool_calls", routeAfterPlanning)
    .addConditionalEdges("execute_tool_calls", routeAfterToolExecution)
    .addEdge("finalize", END)
    .compile({ checkpointer });

  const runtime = {
    kind: "langgraph",
    graph,
    async invoke(_input, { onEvent, initial }) {
      let state;
      try {
        state = await graph.invoke(
          initial,
          {
            signal: createRunnableSignal(),
            configurable: {
              thread_id: JSON.stringify([initial.threadId, initial.actorScope]),
              onEvent,
            },
          },
        );
      } catch (error) {
        await stages.persistFailure(initial, error);
        throw error;
      }
      await stages.persistState(state);
      return stages.resultFromState(state);
    },
  };
  return { graph, ...wrapScopedChatRuntime({ kind: "langgraph", prepareInvocation: stages.prepareInvocation, invoke: runtime.invoke }) };
}
