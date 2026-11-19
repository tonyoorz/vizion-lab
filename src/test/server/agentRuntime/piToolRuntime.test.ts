// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { spawnSync } from "node:child_process";
import fs from "node:fs";

import * as orchestrator from "../../../../server/mainAgentToolOrchestrator.mjs";
import * as piRuntime from "../../../../server/agentRuntime/piToolRuntime.mjs";
import { fingerprintQueryPlanSteps } from "../../../../server/ontology/queryPlanner.mjs";
import { streamAgentChatResponse } from "../../../../server/agentRuntime/langGraphChatHandler.mjs";
import { createLangGraphChatRuntime } from "../../../../server/agentRuntime/langGraphChatRuntime.mjs";
import { resolveAiAnalyticsContext } from "../../../../server/aiAnalyticsContext.mjs";
import { MAIN_AGENT_TOOLS } from "../../../../server/mainAgentTools.mjs";
import { classifyDirectMainAgentIntent } from "../../../../server/mainAgentDirectIntent.mjs";
import { shouldPlanMainAgentTools } from "../../../../server/mainAgentToolPlanning.mjs";

function readOnlyToolset(toolNames = ["get_ontology_catalog", "ask_clarification"]) {
  return {
    intent: "schema_discovery",
    toolNames,
    tools: MAIN_AGENT_TOOLS.filter((tool) => toolNames.includes(tool.function.name)),
  };
}

async function runPiTurn(options: Record<string, unknown>) {
  expect(orchestrator).toHaveProperty("runPiMainAgentToolTurn", expect.any(Function));
  return Reflect.get(orchestrator, "runPiMainAgentToolTurn")(options);
}

function plannedCall(name: string, args: unknown = {}, id = `${name}-1`) {
  return { id, type: "function", function: { name, arguments: JSON.stringify(args) } };
}

function toolResult(call: ReturnType<typeof plannedCall>, payload: unknown = { ok: true }) {
  return {
    contextText: `# Main agent tool result\nTool: ${call.function.name}\nSynthetic result`,
    toolMessage: { role: "tool", tool_call_id: call.id, name: call.function.name, content: JSON.stringify(payload) },
  };
}

const conversation = [{ role: "user", content: "Show the governed defect count." }];

describe("context-aware conversation admission", () => {
  it("leaves domain interpretation to the model instead of rejecting programming words", () => {
    expect(classifyDirectMainAgentIntent("Python automation testing coverage")).toBeNull();
    expect(shouldPlanMainAgentTools([{ role: "user", content: "Python automation testing coverage" }])).toBe(true);
  });

  it.each(["\u90a3\u4e0a\u5468\u5462", "\u7ee7\u7eed", "\u6362\u6210\u53bb\u5e74"])("admits a contextual follow-up: %s", (query) => {
    expect(shouldPlanMainAgentTools([
      ...conversation,
      { role: "assistant", content: "The previous authorized result is available." },
      { role: "user", content: query },
    ])).toBe(true);
  });
});

describe("Pi production Chat runtime", () => {
  const actor = {
    actorId: "migration-reader", scopeHash: "migration-scope",
    scopes: { workspaceIds: ["DTSV"], allowedObjectTypes: ["quality.defect"] },
  };
  const body = { actor, messages: conversation, useAnalyticsContext: true, threadId: "production-thread" };
  const createRuntime = (options: Record<string, unknown> = {}) => Reflect.get(piRuntime, "createPiChatRuntime")({
    resolveAnalyticsContext: vi.fn().mockResolvedValue({ contextText: "# Scoped context", skipDefectContext: false }),
    shouldPlanTools: () => true,
    requestAnswerCompletion: vi.fn().mockResolvedValue({ content: "The authorized request has completed.", toolCalls: [] }),
    ...options,
  });

  it("preserves a completed conversational answer instead of asking another model to replace it", async () => {
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "We can review the prior result together.", toolCalls: [], answerModel: "deepseek-v4-pro" });
    const requestAnswerCompletion = vi.fn();
    const history = [...conversation, { role: "assistant", content: "Previous result." }, { role: "user", content: "Explain that result." }];
    const result = await createRuntime({ requestToolCompletion, requestAnswerCompletion }).invoke({ body: { ...body, messages: history } });
    expect(result.preparedCompletion).toMatchObject({ content: "We can review the prior result together.", answerModel: "deepseek-v4-pro" });
    expect(requestToolCompletion).toHaveBeenCalledTimes(1);
    expect(requestToolCompletion.mock.calls[0][0].messages).toEqual(history);
    expect(requestAnswerCompletion).not.toHaveBeenCalled();
  });

  it("does not manufacture a semantic data request while preparing an ordinary greeting", async () => {
    const requestSemanticCandidate = vi.fn().mockResolvedValue(null);
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "Hello. How can I help?", toolCalls: [] });
    const executeToolCall = vi.fn(async (call) => toolResult(call));
    const resolveDefectContext = vi.fn();
    const runtime = createRuntime({
      resolveAnalyticsContext: (parameters) => resolveAiAnalyticsContext({ ...parameters, requestSemanticCandidate }),
      requestToolCompletion, executeToolCall, resolveDefectContext,
    });
    const result = await runtime.invoke({ body: { ...body, useDefectContext: true, messages: [{ role: "user", content: "Hello" }] } });
    expect(requestSemanticCandidate).not.toHaveBeenCalled();
    expect(executeToolCall).not.toHaveBeenCalled();
    expect(resolveDefectContext).not.toHaveBeenCalled();
    expect(requestToolCompletion).toHaveBeenCalledTimes(1);
    expect(result.preparedCompletion.content).toBe("Hello. How can I help?");
    expect(result.analyticsContext.queryPlan).toBeUndefined();
    expect(requestToolCompletion.mock.calls[0][0].context).toContain("schemaFingerprint");
  });

  it("does not advertise a data tool whose backend operation is unavailable", async () => {
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "The available data tools are ready.", toolCalls: [] });
    const runtime = createRuntime({ requestToolCompletion, discoverTools: async () => ({
      availableToolNames: ["get_data_catalog", "query_semantic_metrics", "query_semantic_records", "ask_clarification"],
      unavailableToolNames: ["query_defect_aggregate", "get_ontology_catalog"],
    }) });
    await runtime.invoke({ body });
    const request = requestToolCompletion.mock.calls[0][0];
    expect(request.tools.map((tool: { function: { name: string } }) => tool.function.name)).not.toContain("query_defect_aggregate");
    expect(request.context).toContain("unavailable");
  });

  it("lets the model handle a reporter question with the available semantic tools", async () => {
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "I can inspect the authorized semantic fields for this reporter.", toolCalls: [] });
    const executeToolCall = vi.fn();
    const runtime = createRuntime({ requestToolCompletion, executeToolCall, discoverTools: async () => ({
      availableToolNames: ["get_data_catalog", "query_semantic_metrics", "query_semantic_records", "ask_clarification"],
      unavailableToolNames: ["search_analytics_filter_values", "query_analytics"],
    }) });
    const messages = [{ role: "user", content: "2026 xumiao \u63d0\u7968\u60c5\u51b5" }];
    const result = await runtime.invoke({ body: { ...body, messages } });
    expect(requestToolCompletion).toHaveBeenCalledOnce();
    expect(requestToolCompletion.mock.calls[0][0].messages).toEqual(messages);
    expect(requestToolCompletion.mock.calls[0][0].tools.map((tool: { function: { name: string } }) => tool.function.name))
      .toEqual(expect.arrayContaining(["query_semantic_metrics", "query_semantic_records"]));
    expect(executeToolCall).not.toHaveBeenCalled();
    expect(result.preparedCompletion.content).toContain("authorized semantic fields");
    expect(requestToolCompletion.mock.calls[0][0].context).not.toContain("Routing priority:");
    expect(requestToolCompletion.mock.calls[0][0].context).toContain("without a fixed business workflow");
  });

  it("allows discovery and correction beyond four default tool rounds", async () => {
    const calls = Array.from({ length: 5 }, (_, index) => plannedCall("resolve_business_terms", { query: `discovery-${index}` }, `discovery-${index}`));
    let turn = 0;
    const requestToolCompletion = vi.fn(async () => turn < calls.length
      ? { content: "", toolCalls: [calls[turn++]] }
      : { content: "The five authorized discovery steps are complete.", toolCalls: [] });
    const executeToolCall = vi.fn(async (call) => toolResult(call));
    const result = await createRuntime({ requestToolCompletion, executeToolCall }).invoke({ body });
    expect(executeToolCall).toHaveBeenCalledTimes(5);
    expect(requestToolCompletion).toHaveBeenCalledTimes(6);
    expect(result.preparedCompletion.content).toBe("The five authorized discovery steps are complete.");
  });

  it.each([
    { name: "query_analytics", args: { dataset: "defects", intent: "aggregate", metrics: ["defect_count"], dimensions: [], filters: {}, time: {} } },
    { name: "query_defect_aggregate", args: { metrics: ["defect_count"], dimensions: [], filters: {}, time: {} } },
    { name: "query_analytics_fallback", args: { dataset: "defects", metrics: [{ op: "count", field: "defect_id", as: "defect_count" }], group_by: [], filters: {}, time: {} } },
  ])("allows an all-time query without inventing optional dates: $name", async ({ name, args }) => {
    const call = plannedCall(name, args, "all-time-query");
    const requestToolCompletion = vi.fn().mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValueOnce({ content: "The authorized all-time result is ready.", toolCalls: [] });
    const executeToolCall = vi.fn().mockResolvedValue(toolResult(call, { ok: true, result: { returned_groups: 1, rows: [{ defect_count: 3 }] } }));
    await createRuntime({ requestToolCompletion, executeToolCall }).invoke({ body });
    expect(executeToolCall).toHaveBeenCalledOnce();
    expect(JSON.parse(executeToolCall.mock.calls[0][0].function.arguments).time).toEqual({});
  });

  it("returns invalid tool arguments to the model for a corrected permitted call", async () => {
    const invalid = plannedCall("get_ontology_catalog", { unexpected: true }, "invalid-arguments");
    const corrected = plannedCall("get_data_catalog", {}, "corrected-catalog");
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [invalid] })
      .mockResolvedValueOnce({ content: "", toolCalls: [corrected] })
      .mockResolvedValueOnce({ content: "Recovered using an available catalog.", toolCalls: [] });
    const executeToolCall = vi.fn(async (call) => toolResult(call));
    const requestAnswerCompletion = vi.fn().mockResolvedValue({ content: "Stopped before recovery.", toolCalls: [] });
    const result = await createRuntime({ requestToolCompletion, executeToolCall, requestAnswerCompletion }).invoke({ body });
    expect(requestToolCompletion).toHaveBeenCalledTimes(3);
    expect(executeToolCall).toHaveBeenCalledOnce();
    expect(executeToolCall.mock.calls[0][0].function.name).toBe("get_data_catalog");
    expect(requestToolCompletion.mock.calls[1][0].messages).toEqual(expect.arrayContaining([
      expect.objectContaining({ role: "tool", tool_call_id: "invalid-arguments" }),
    ]));
    expect(result.preparedCompletion.content).toBe("Recovered using an available catalog.");
    expect(requestAnswerCompletion).not.toHaveBeenCalled();
  });

  it("lets the model correct a recoverable field error without a fixed retry script", async () => {
    const query = { dataset: "defects", intent: "aggregate", metrics: ["defect_count"], dimensions: [], filters: { detected_by: ["Unmatched Reporter"] },
      time: { field: "creation_time", current: ["2026-01-01", "2026-12-31"], timezone: "Asia/Shanghai" } };
    const failed = plannedCall("query_analytics", query, "unmatched-query");
    const corrected = plannedCall("query_analytics", { ...query, filters: { detected_by: ["Matched Reporter"] } }, "matched-query");
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [failed] })
      .mockResolvedValueOnce({ content: "", toolCalls: [corrected] })
      .mockResolvedValueOnce({ content: "The corrected authorized query returned a result.", toolCalls: [] });
    const executeToolCall = vi.fn()
      .mockResolvedValueOnce(toolResult(failed, { ok: false, code: "UNSUPPORTED_FILTER", error: "The reporter filter value could not be resolved." }))
      .mockResolvedValueOnce(toolResult(corrected, { ok: true, result: { returned_groups: 1, rows: [{ defect_count: 3 }] } }));
    const result = await createRuntime({ requestToolCompletion, executeToolCall }).invoke({ body });
    expect(requestToolCompletion).toHaveBeenCalledTimes(3);
    expect(executeToolCall).toHaveBeenCalledTimes(2);
    expect(requestToolCompletion.mock.calls[1][0].messages).toEqual(expect.arrayContaining([
      expect.objectContaining({ role: "tool", tool_call_id: "unmatched-query", content: expect.stringContaining("TOOL_SCHEMA_OR_VALUE_MISMATCH") }),
    ]));
    expect(result.preparedCompletion.content).toContain("corrected authorized query");
  });

  it("composes a terminal tool result within the same Pi turn using answer-only instructions", async () => {
    const call = plannedCall("get_ontology_catalog");
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "", toolCalls: [call] });
    const requestAnswerCompletion = vi.fn().mockResolvedValue({ content: "Catalog evidence is available.", toolCalls: [] });
    const result = await createRuntime({
      requestToolCompletion, requestAnswerCompletion, maxToolSteps: 1,
      executeToolCall: vi.fn().mockResolvedValue(toolResult(call)),
    }).invoke({ body });
    expect(result.preparedCompletion).toMatchObject({ content: "Catalog evidence is available." });
    expect(requestToolCompletion).toHaveBeenCalledTimes(1);
    expect(requestAnswerCompletion).toHaveBeenCalledTimes(1);
    expect(requestAnswerCompletion.mock.calls[0][0]).toMatchObject({
      messages: conversation, tools: [], context: expect.stringContaining("Synthetic result"),
    });
  });

  it("does not load LangGraph on the production Pi path", () => {
    const source = `
      import { registerHooks } from 'node:module';
      registerHooks({ resolve(specifier, context, nextResolve) {
        if (specifier === '@langchain/langgraph') throw new Error('LangGraph loaded on Pi path');
        return nextResolve(specifier, context);
      }});
      const { createPiChatRuntime } = await import('./server/agentRuntime/piChatRuntime.mjs');
      console.log(createPiChatRuntime({ ontologyRegistry: null }).kind);
    `;
    const completed = spawnSync(process.execPath, ["--input-type=module"], { input: source, encoding: "utf8" });
    expect(completed.status, completed.stderr).toBe(0);
    expect(completed.stdout.trim()).toBe("pi");
  });

  it("invokes the authenticated Chat contract through Pi without a LangGraph graph", async () => {
    expect(piRuntime).toHaveProperty("createPiChatRuntime", expect.any(Function));
    const catalogCall = plannedCall("get_ontology_catalog");
    const actor = {
      actorId: "migration-reader", scopeHash: "migration-scope",
      scopes: { workspaceIds: ["DTSV"], allowedObjectTypes: ["quality.defect"] },
    };
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [catalogCall] })
      .mockResolvedValueOnce({ content: "Done.", toolCalls: [] });
    const executeToolCall = vi.fn().mockResolvedValue(toolResult(catalogCall));
    const resolveAnalyticsContext = vi.fn().mockResolvedValue({ contextText: "# Scoped analytics", skipDefectContext: false });
    const runtimeStore = {
      appendRunEvent: vi.fn(), appendToolAudit: vi.fn(), appendRunSummary: vi.fn(), writeThreadCheckpoint: vi.fn(),
    };
    const runtime = Reflect.get(piRuntime, "createPiChatRuntime")({
      resolveAnalyticsContext, requestToolCompletion, executeToolCall, runtimeStore,
      shouldPlanTools: () => true, now: () => new Date("2026-11-16T08:00:00Z"),
    });
    const result = await runtime.invoke({
      body: {
        messages: conversation, actor, useAnalyticsContext: true,
        context: "# User context", threadId: "migration-thread", runId: "migration-run",
      },
    });
    expect(runtime.kind).toBe("pi");
    expect(runtime.graph).toBeUndefined();
    expect(result.runtime).toBe("pi");
    expect(result.actorScope).toEqual(actor);
    expect(result.context).toContain("# Scoped analytics");
    expect(result.context).toContain("# User context");
    expect(result.context).toContain("Synthetic result");
    expect(result.finalMessages).toEqual(conversation);
    expect(result.metrics).toMatchObject({ runtime: "pi", mainAgentToolCallCount: 1 });
    expect(result.piEvents.map((event: { type: string }) => event.type)).toContain("tool_execution_end");
    expect(executeToolCall).toHaveBeenCalledWith(catalogCall, expect.objectContaining({ actor }));
    expect(runtimeStore.appendToolAudit).toHaveBeenCalledWith(expect.objectContaining({ actorScope: actor, toolName: "get_ontology_catalog" }));
    expect(runtimeStore.writeThreadCheckpoint).toHaveBeenCalledWith(expect.objectContaining({ threadId: "migration-thread", actorScope: actor }));
  });

  it("preserves semantic continuation within a thread but isolates another actor", async () => {
    const fixture = JSON.parse(fs.readFileSync("evals/main-agent/target/execution-golden.jsonl", "utf8").split("\n")[0]);
    const call = plannedCall("query_semantic_metrics", fixture.plans[0][0].arguments, "continuation-query");
    const payload = { ...fixture.fakeResponses[0].body, scope: { ...fixture.fakeResponses[0].body.scope, actorScopeHash: actor.scopeHash } };
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValue({ content: "Done.", toolCalls: [] });
    const runtime = createRuntime({ requestToolCompletion, executeToolCall: vi.fn().mockResolvedValue(toolResult(call, { ok: true, result: payload })) });
    await runtime.invoke({ body });
    await runtime.invoke({ body: { ...body, messages: [{ role: "user", content: "Show those records." }] } });
    expect(requestToolCompletion.mock.calls[2][0].context).toContain(`analysis_ref: ${payload.analysisRef}`);
    await runtime.invoke({ body: { ...body, actor: { ...actor, actorId: "other-reader", scopeHash: "other-scope" } } });
    expect(requestToolCompletion.mock.calls[3][0].context).not.toContain(payload.analysisRef);
  });

  it.each([false, true])("keeps history-based answer diagnostics advisory: ordinary=%s", async (ordinary) => {
    const fixture = JSON.parse(fs.readFileSync("evals/main-agent/target/execution-golden.jsonl", "utf8").split("\n")[0]);
    const call = plannedCall("query_semantic_metrics", fixture.plans[0][0].arguments, "history-query");
    const payload = { ...fixture.fakeResponses[0].body, scope: { ...fixture.fakeResponses[0].body.scope, actorScopeHash: actor.scopeHash } };
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValueOnce({ content: '<cite source="history-query">3 defects</cite>', toolCalls: [] })
      .mockResolvedValueOnce({ content: ordinary ? '<cite source="history-query">We can review the result together.</cite>' : '<cite source="history-query">999999 defects; D-999</cite>', toolCalls: [] });
    const runtime = createRuntime({ requestToolCompletion, executeToolCall: vi.fn().mockResolvedValue(toolResult(call, { ok: true, result: payload })) });
    await runtime.invoke({ body });
    const response = { write: vi.fn(), writeHead: vi.fn(), end: vi.fn() };
    const completed = await streamAgentChatResponse({
      body: { ...body, messages: [...conversation, { role: "assistant", content: "Previous result." }, { role: "user", content: "Explain that result." }] },
      runtime, response,
    });
    expect(completed.answerValidation).toEqual(expect.objectContaining({ valid: ordinary }));
    expect(requestToolCompletion.mock.calls[2][0].context).toContain("history-query");
    const output = response.write.mock.calls.map(([chunk]) => String(chunk)).join("");
    if (ordinary) expect(output).toContain("We can review the result together.");
    else {
      expect(output).toContain("999999 defects");
      expect(output).toContain("D-999");
    }
    expect(completed.runtimeResult.preparedCompletion).not.toHaveProperty("evidenceFallback");
  });

  it("does not regenerate or replace an answer after advisory evidence diagnostics", async () => {
    const fixture = JSON.parse(fs.readFileSync("evals/main-agent/target/execution-golden.jsonl", "utf8").split("\n")[0]);
    const call = plannedCall("query_semantic_metrics", fixture.plans[0][0].arguments, "repair-query");
    const payload = { ...fixture.fakeResponses[0].body, scope: { ...fixture.fakeResponses[0].body.scope, actorScopeHash: actor.scopeHash } };
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValueOnce({ content: "There are 3 defects.", toolCalls: [] });
    const requestAnswerCompletion = vi.fn().mockRejectedValue(new Error("Unexpected forced answer correction"));
    const executeToolCall = vi.fn().mockResolvedValue(toolResult(call, { ok: true, result: payload }));
    const runtime = createRuntime({ requestToolCompletion, requestAnswerCompletion, executeToolCall });
    const response = { write: vi.fn(), writeHead: vi.fn(), end: vi.fn() };
    const completed = await streamAgentChatResponse({ body, response, runtime });
    expect(requestToolCompletion).toHaveBeenCalledTimes(2);
    expect(executeToolCall).toHaveBeenCalledTimes(1);
    expect(requestAnswerCompletion).not.toHaveBeenCalled();
    expect(completed.answerValidation).toMatchObject({ valid: false, violations: expect.arrayContaining(["ANSWER_NUMERIC_CLAIM_UNCITED"]) });
    expect(completed.runtimeResult.preparedCompletion.content).toBe("There are 3 defects.");
    expect(completed.runtimeResult.preparedCompletion).not.toHaveProperty("evidenceFallback");
    expect(response.write.mock.calls.map(([chunk]) => String(chunk)).join("")).toContain("There are 3 defects.");
  });

  it("does not replace a failed current semantic attempt with historical success", async () => {
    const fixture = JSON.parse(fs.readFileSync("evals/main-agent/target/execution-golden.jsonl", "utf8").split("\n")[0]);
    const call = plannedCall("query_semantic_metrics", fixture.plans[0][0].arguments, "previous-query");
    const payload = { ...fixture.fakeResponses[0].body, scope: { ...fixture.fakeResponses[0].body.scope, actorScopeHash: actor.scopeHash } };
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValueOnce({ content: '<cite source="previous-query">3 defects</cite>', toolCalls: [] })
      .mockResolvedValueOnce({ content: "", toolCalls: [plannedCall("query_semantic_metrics", {}, "invalid-current-query")] })
      .mockResolvedValueOnce({ content: "The current query failed. Please confirm the reporter match.", toolCalls: [] });
    const runtime = createRuntime({ requestToolCompletion,
      executeToolCall: vi.fn().mockResolvedValue(toolResult(call, { ok: true, result: payload })),
      requestAnswerCompletion: vi.fn().mockResolvedValue({ content: "The current query failed. Please confirm the reporter match.", toolCalls: [] }),
    });
    await runtime.invoke({ body });
    const response = { write: vi.fn(), writeHead: vi.fn(), end: vi.fn() };
    const completed = await streamAgentChatResponse({
      body: { ...body, messages: [...conversation, { role: "assistant", content: "Previous result." }, { role: "user", content: "Check the changed scope." }] },
      runtime, response,
    });
    expect(completed.runtimeResult.mainAgentToolContext.evidenceGate.status).toBe("blocked");
    const output = response.write.mock.calls.map(([chunk]) => String(chunk)).join("");
    expect(output).not.toContain("3 defects");
    expect(output).toContain("The current query failed.");
    expect(output).not.toContain("未发布未经证据验证的数据结论");
  });

  it("serializes concurrent turns within the same actor-scoped thread", async () => {
    let release = () => {};
    let entered = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const started = new Promise<void>((resolve) => { entered = resolve; });
    const resolveAnalyticsContext = vi.fn()
      .mockImplementationOnce(async () => { entered(); await gate; return { contextText: "first" }; })
      .mockResolvedValue({ contextText: "second" });
    const runtime = createRuntime({ resolveAnalyticsContext, shouldPlanTools: () => false });
    const first = runtime.invoke({ body });
    await started;
    const second = runtime.invoke({ body });
    await Promise.resolve();
    const beforeRelease = resolveAnalyticsContext.mock.calls.length;
    release();
    await Promise.all([first, second]);
    expect(beforeRelease).toBe(1);
  });

  it.each(["pi", "langgraph"])("keeps the %s thread lease until final answer streaming and validation settle", async (kind) => {
    let release = () => {};
    let entered = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const started = new Promise<void>((resolve) => { entered = resolve; });
    const resolveAnalyticsContext = vi.fn().mockResolvedValue({ contextText: "# Synthetic context" });
    const runtime = kind === "pi" ? createRuntime({ resolveAnalyticsContext, shouldPlanTools: () => false })
      : createLangGraphChatRuntime({ resolveAnalyticsContext, shouldPlanTools: () => false });
    const response = { write: vi.fn(), writeHead: vi.fn(), end: vi.fn() };
    const first = streamAgentChatResponse({
      body, response, runtime, streamCompletion: async () => { entered(); await gate; },
    });
    await started;
    const second = runtime.invoke({ body });
    await new Promise((resolve) => setImmediate(resolve));
    const beforeRelease = resolveAnalyticsContext.mock.calls.length;
    release();
    await Promise.all([first, second]);
    expect(beforeRelease).toBe(1);
  });

  it("allows a new turn to refresh an identical query while deduplicating within each turn", async () => {
    const call = plannedCall("get_ontology_catalog");
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [call] }).mockResolvedValueOnce({ content: "Done.", toolCalls: [] })
      .mockResolvedValueOnce({ content: "", toolCalls: [call] }).mockResolvedValueOnce({ content: "Done.", toolCalls: [] });
    const executeToolCall = vi.fn().mockResolvedValue(toolResult(call));
    const runtime = createRuntime({ requestToolCompletion, executeToolCall });
    const first = await runtime.invoke({ body });
    const second = await runtime.invoke({ body });
    expect(executeToolCall).toHaveBeenCalledTimes(2);
    expect(first.mainAgentToolContext.toolCalls).toHaveLength(1);
    expect(second.mainAgentToolContext.toolCalls).toHaveLength(1);
  });

  it("lets the model request empty-result diagnostics again in a later turn", async () => {
    const args = {
      dataset: "defects", intent: "aggregate", metrics: ["defect_count"], dimensions: [], filters: {},
      time: { field: "creation_time", current: ["2026-11-01", "2026-11-16"], timezone: "Asia/Shanghai" },
    };
    const call = plannedCall("query_analytics", args);
    const diagnosis = plannedCall("diagnose_analytics_empty", { query: args }, "model-selected-diagnosis");
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValueOnce({ content: "", toolCalls: [diagnosis] })
      .mockResolvedValueOnce({ content: "Done.", toolCalls: [] })
      .mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValueOnce({ content: "", toolCalls: [diagnosis] })
      .mockResolvedValueOnce({ content: "Done.", toolCalls: [] });
    const executeToolCall = vi.fn(async (item) => toolResult(item, {
      ok: true, result: item.function.name === "query_analytics" ? { rows: [], returned_groups: 0 } : { causeCode: "NO_SINGLE_FILTER_RECOVERY" },
    }));
    const runtime = createRuntime({ requestToolCompletion, executeToolCall });
    await runtime.invoke({ body });
    const second = await runtime.invoke({ body });
    expect(second.mainAgentToolContext.toolCalls.map((item: ReturnType<typeof plannedCall>) => item.function.name))
      .toEqual(["query_analytics", "diagnose_analytics_empty"]);
  });

  it("does not resolve data or plan tools without a server-owned actor scope", async () => {
    const resolveAnalyticsContext = vi.fn();
    const requestToolCompletion = vi.fn();
    const executeToolCall = vi.fn();
    const runtime = createRuntime({ resolveAnalyticsContext, requestToolCompletion, executeToolCall });
    const result = await runtime.invoke({ body: { ...body, actor: {} } });
    expect(result.directResponse.intent).toBe("data_access_unavailable");
    expect(resolveAnalyticsContext).not.toHaveBeenCalled();
    expect(requestToolCompletion).not.toHaveBeenCalled();
    expect(executeToolCall).not.toHaveBeenCalled();
  });

  it("publishes a blocked-tool event instead of executing an unregistered tool", async () => {
    const executeToolCall = vi.fn();
    const runtime = createRuntime({
      executeToolCall, requestToolCompletion: vi.fn().mockResolvedValue({ content: "", toolCalls: [plannedCall("bash")] }),
    });
    const result = await runtime.invoke({ body });
    expect(executeToolCall).not.toHaveBeenCalled();
    expect(result.prefaceEvents).toContainEqual(expect.objectContaining({ type: "tool-blocked", toolName: "bash", reason: "tool_not_allowed" }));
  });

  it("executes a complete tool batch before enforcing the model step limit", async () => {
    const calls = [plannedCall("get_ontology_catalog"), plannedCall("get_data_catalog"), plannedCall("resolve_business_terms", { query: "defect count" })];
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "", toolCalls: calls });
    const executeToolCall = vi.fn(async (call) => toolResult(call));
    const result = await createRuntime({ requestToolCompletion, executeToolCall, maxToolSteps: 1 }).invoke({ body });
    expect(executeToolCall).toHaveBeenCalledTimes(3);
    expect(requestToolCompletion).toHaveBeenCalledTimes(1);
    expect(result.mainAgentToolContext.stoppedReason).toBe("max_steps");
  });

  it("keeps attachment-bearing conversation messages for planning and final SSE extraction", async () => {
    const messages = [{ role: "user", content: [{ type: "text", text: "Show defect counts." }, { type: "image_url", image_url: { url: "data:image/png;base64,cG9j" } }] }];
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "Done.", toolCalls: [] });
    const result = await createRuntime({ requestToolCompletion }).invoke({ body: { ...body, messages } });
    expect(requestToolCompletion.mock.calls[0][0].messages).toEqual(messages);
    expect(result.finalMessages).toEqual(messages);
  });

  it("persists a failed runtime audit when the company planner fails", async () => {
    const runtimeStore = { appendRunEvent: vi.fn(), appendRunSummary: vi.fn() };
    const runtime = createRuntime({
      runtimeStore, requestToolCompletion: vi.fn().mockRejectedValue(new Error("Company planner unavailable")),
    });
    await expect(runtime.invoke({ body })).rejects.toThrow("Company planner unavailable");
    expect(runtimeStore.appendRunEvent).toHaveBeenCalledWith(expect.objectContaining({ type: "agent-runtime-failed", actorScope: actor }));
  });

  it.each(["query_semantic_metrics", "query_semantic_records"])("executes a ready scope-bound %s plan without model replanning", async (toolName) => {
    const fixture = JSON.parse(fs.readFileSync("evals/main-agent/target/execution-golden.jsonl", "utf8").split("\n")[0]);
    const query = { ...fixture.plans[0][0].arguments.query, schemaFingerprint: "a".repeat(64) };
    const args = toolName === "query_semantic_metrics" ? { query } : {
      ontology_version: "v1", schema_fingerprint: query.schemaFingerprint, query: { ...query, intent: "list" },
      analysis_ref: null, selections: [], fields: ["defect_id", "name"], page: 1, page_size: 20,
    };
    const analysisPlan = {
      schemaVersion: "1.0", analysisPlanId: `analysis-${"c".repeat(16)}`, sourcePlanId: `plan-${"d".repeat(16)}`,
      sourcePlanFingerprint: "e".repeat(64), ontologyVersion: "v1", schemaFingerprint: query.schemaFingerprint,
      status: "ready", operation: toolName === "query_semantic_metrics" ? "group_comparison" : "record_table",
      visualization: toolName === "query_semantic_metrics" ? "grouped_bar" : "table", maxRows: 20,
      guardrails: ["READ_ONLY_SOURCE_PLAN", "NO_ARBITRARY_CODE", "NO_ARBITRARY_SQL", "NO_CAUSAL_CLAIMS"], ruleEffects: [],
    };
    const requestToolCompletion = vi.fn();
    const executeToolCall = vi.fn(async (call) => toolResult(call));
    const resolveAnalyticsContext = vi.fn().mockResolvedValue({
      contextText: "# Ready governed plan", skipDefectContext: false, analysisPlan,
      queryPlan: { status: "valid", actorScopeHash: actor.scopeHash, planId: analysisPlan.sourcePlanId,
        steps: [{ stepId: "s1", operation: toolName === "query_semantic_metrics" ? "semantic_metric_query" : "semantic_record_query", toolName, canonicalArgs: args }] },
    });
    const result = await createRuntime({ resolveAnalyticsContext, requestToolCompletion, executeToolCall }).invoke({ body });
    expect(requestToolCompletion).not.toHaveBeenCalled();
    expect(executeToolCall).toHaveBeenCalledTimes(1);
    expect(result.mainAgentToolContext.toolCalls).toHaveLength(1);
    expect(result.mainAgentToolContext.toolCalls[0].function).toEqual({ name: toolName, arguments: JSON.stringify(args) });
    expect(result.mainAgentToolContext.stoppedReason).toBe("deterministic_plan_complete");
  });

  it("keeps existing coverage, duplicate, testcase context, and record export tools available", async () => {
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "Done.", toolCalls: [] });
    await createRuntime({ requestToolCompletion }).invoke({ body });
    const names = requestToolCompletion.mock.calls[0][0].tools.map((tool: { function: { name: string } }) => tool.function.name);
    expect(names).toEqual(expect.arrayContaining([
      "query_testing_team_fv_analysis", "query_testing_coverage_project_status", "query_testing_coverage_aida_status",
      "search_duplicates", "get_test_case_context", "export_defect_records",
    ]));
    expect(names).not.toContain("bash");
  });

  it("executes the schema-validated arguments rather than the original model value", async () => {
    const call = plannedCall("ask_clarification", { question: "Choose one?", options: "A" });
    const executeToolCall = vi.fn(async (item) => ({ ...toolResult(item), requiresUserInput: true }));
    await createRuntime({ executeToolCall, requestToolCompletion: vi.fn().mockResolvedValue({ content: "", toolCalls: [call] }) }).invoke({ body });
    expect(JSON.parse(executeToolCall.mock.calls[0][0].function.arguments).options).toEqual(["A"]);
  });

  it("rejects duplicate call IDs before executing any batch tool", async () => {
    const executeToolCall = vi.fn();
    const calls = [plannedCall("get_ontology_catalog", {}, "same-id"), plannedCall("ask_clarification", { question: "Which scope?" }, "same-id")];
    await expect(createRuntime({ executeToolCall, requestToolCompletion: vi.fn().mockResolvedValue({ content: "", toolCalls: calls }) }).invoke({ body }))
      .rejects.toThrow("tool call identifiers");
    expect(executeToolCall).not.toHaveBeenCalled();
  });

  it("records only attempted canonical steps when the first step is denied", async () => {
    const fixture = JSON.parse(fs.readFileSync("evals/main-agent/target/execution-golden.jsonl", "utf8").split("\n")[0]);
    const query = fixture.plans[0][0].arguments.query;
    const steps = [0, 1].map((index) => ({
      stepId: `s${index + 1}`, operation: "semantic_metric_query", toolName: "query_semantic_metrics",
      canonicalArgs: { query: { ...query, dimensionIds: index ? [] : query.dimensionIds } },
      metricIds: query.metricIds, dimensionIds: index ? [] : query.dimensionIds, dependsOn: [], riskLevel: "R0",
    }));
    const semanticPlan = {
      schemaVersion: "1.0", planId: "plan-canonical-denial", version: 1, status: "valid", ontologyVersion: "v1",
      schemaFingerprint: "f".repeat(64), actorScopeHash: actor.scopeHash, sourceQueryFingerprint: "a".repeat(64),
      executionFingerprint: fingerprintQueryPlanSteps(steps), steps, violations: [], warnings: [], ruleEffects: [],
    };
    const executeToolCall = vi.fn(async (call) => toolResult(call, { ok: false, code: "ACTOR_SCOPE_DENIED", error: "Scope denied", statusCode: 403 }));
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "Done.", toolCalls: [] });
    const runtime = createRuntime({ executeToolCall, requestToolCompletion,
      resolveAnalyticsContext: vi.fn().mockResolvedValue({ contextText: "canonical", semanticPlan, skipDefectContext: false }) });
    const result = await runtime.invoke({ body });
    expect(executeToolCall).toHaveBeenCalledTimes(1);
    expect(requestToolCompletion).not.toHaveBeenCalled();
    expect(result.mainAgentToolContext.toolCalls.map((call: ReturnType<typeof plannedCall>) => call.function.name)).toEqual(["query_semantic_metrics"]);
    expect(result.metrics.mainAgentToolCallCount).toBe(1);
  });

  it("does not suppress a model-selected reporter clarification because of a prior turn", async () => {
    const executeToolCall = vi.fn(async (call) => call.function.name === "ask_clarification"
      ? { ...toolResult(call), requiresUserInput: true }
      : toolResult(call, { ok: true, result: { values: [{ value: "Person A" }, { value: "Person B" }] } }));
    const clarification = plannedCall("ask_clarification", { question: "Which reporter do you mean?" }, "model-reporter-clarification");
    const runtime = createRuntime({ executeToolCall, requestToolCompletion: vi.fn().mockResolvedValue({ content: "", toolCalls: [clarification] }) });
    const reporterBody = { ...body, messages: [{ role: "user", content: "Xumiao \u63d0\u7968\u60c5\u51b5" }] };
    const first = await runtime.invoke({ body: reporterBody });
    expect(first.mainAgentToolContext.stoppedReason).toBe("clarification_requested");
    const second = await runtime.invoke({ body: reporterBody });
    expect(second.mainAgentToolContext.stoppedReason).toBe("clarification_requested");
    expect(second.mainAgentToolContext.toolCalls.map((call: ReturnType<typeof plannedCall>) => call.function.name))
      .toEqual(["ask_clarification"]);
  });

  it("preserves already-executed tool audits when later planning fails", async () => {
    const call = plannedCall("get_ontology_catalog");
    const runtimeStore = { appendRunEvent: vi.fn(), appendRunSummary: vi.fn(), appendToolAudit: vi.fn() };
    const requestToolCompletion = vi.fn().mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockRejectedValueOnce(new Error("Later planner failure"));
    await expect(createRuntime({ runtimeStore, requestToolCompletion, executeToolCall: vi.fn().mockResolvedValue(toolResult(call)) }).invoke({ body }))
      .rejects.toThrow("Later planner failure");
    expect(runtimeStore.appendToolAudit).toHaveBeenCalledWith(expect.objectContaining({ toolName: "get_ontology_catalog", actorScope: actor }));
    expect(runtimeStore.appendRunSummary).toHaveBeenCalledWith(expect.objectContaining({ outcome: "failed", toolNames: ["get_ontology_catalog"] }));
  });

  it("does not reserve tool IDs for a removed automatic diagnosis", async () => {
    const query = {
      dataset: "defects", intent: "aggregate", metrics: ["defect_count"], dimensions: [], filters: {},
      time: { field: "creation_time", current: ["2026-11-01", "2026-11-16"], timezone: "Asia/Shanghai" },
    };
    const calls = [plannedCall("query_analytics", query, "collision"), plannedCall("get_ontology_catalog", {}, "collision-diagnosis")];
    const executeToolCall = vi.fn(async (call) => toolResult(call, {
      ok: true, result: call.function.name === "query_analytics" ? { rows: [], returned_groups: 0 } : { causeCode: "NO_SINGLE_FILTER_RECOVERY" },
    }));
    const requestToolCompletion = vi.fn().mockResolvedValueOnce({ content: "", toolCalls: calls }).mockResolvedValueOnce({ content: "Done.", toolCalls: [] });
    const result = await createRuntime({ executeToolCall, requestToolCompletion }).invoke({ body });
    expect(executeToolCall.mock.calls.map(([call]) => call.function.name)).toEqual(["query_analytics", "get_ontology_catalog"]);
    expect(result.preparedCompletion.content).toBe("Done.");
  });

  it("isolates explicit rollback histories by actor scope and publishes only current-turn events", async () => {
    const runtime = createLangGraphChatRuntime({
      resolveAnalyticsContext: vi.fn().mockResolvedValue({ contextText: "# Scoped context" }), shouldPlanTools: () => false,
    });
    await runtime.invoke({ body: { ...body, messages: [{ role: "user", content: "First private quality query" }] } });
    const second = await runtime.invoke({ body: {
      ...body, actor: { ...actor, actorId: "second-reader", scopeHash: "second-scope" },
      messages: [{ role: "user", content: "Second quality query" }],
    } });
    expect(JSON.stringify(second.runtimeEvents)).not.toContain("First private quality query");
  });
});

describe("Pi read-only tool runtime", () => {
  it("runs the company-backed tool turn through Pi and preserves governed results", async () => {
    const catalogCall = {
      id: "catalog-1",
      type: "function",
      function: { name: "get_ontology_catalog", arguments: "{}" },
    };
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [catalogCall] })
      .mockResolvedValueOnce({ content: "Catalog is available.", toolCalls: [] });
    const executeToolCall = vi.fn().mockResolvedValue({
      contextText: "# Main agent tool result\nTool: get_ontology_catalog\nCatalog is available.",
      toolMessage: {
        role: "tool",
        tool_call_id: catalogCall.id,
        name: catalogCall.function.name,
        content: JSON.stringify({ ok: true, tool: "get_ontology_catalog", result: { version: "v1" } }),
      },
    });
    const actor = { actorId: "poc-reader", scopeHash: "poc-read-scope" };

    const result = await runPiTurn({
      messages: [{ role: "user", content: "List available ontology capabilities." }],
      model: "deepseek-v4-flash",
      context: "# Synthetic context",
      selectedToolset: readOnlyToolset(),
      requestToolCompletion,
      executeToolCall,
      toolDependencies: { actor },
      runId: "pi-poc-run",
      threadId: "pi-poc-thread",
    });

    expect(result.runtime).toBe("pi");
    expect(result.toolCalls).toEqual([catalogCall]);
    expect(result.toolMessages).toEqual([expect.objectContaining({ role: "tool", tool_call_id: "catalog-1" })]);
    expect(result.contextText).toContain("Catalog is available.");
    expect(result.evidence).toHaveLength(1);
    expect(result.stoppedReason).toBe("no_tool_calls");
    expect(result.selectedToolset.toolNames).toEqual(readOnlyToolset().toolNames);
    expect(result.toolEvents).toEqual([
      { type: "tool-input-available", toolCallId: "catalog-1", toolName: "get_ontology_catalog", input: {} },
      expect.objectContaining({ type: "tool-output-available", toolCallId: "catalog-1" }),
    ]);
    expect(result.events).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "agent.tool.started", runId: "pi-poc-run", threadId: "pi-poc-thread" }),
      expect.objectContaining({ type: "agent.tool.completed", toolName: "get_ontology_catalog" }),
    ]));
    expect(result.piEvents.map((event: { type: string }) => event.type)).toEqual(expect.arrayContaining([
      "agent_start", "tool_execution_start", "tool_execution_end", "agent_end",
    ]));
    expect(requestToolCompletion).toHaveBeenCalledTimes(2);
    expect(requestToolCompletion.mock.calls[1][0].messages).toEqual(expect.arrayContaining([
      expect.objectContaining({ role: "tool", tool_call_id: "catalog-1" }),
    ]));
    expect(executeToolCall).toHaveBeenCalledWith(catalogCall, { actor });
  });

  it("does not expose or execute filesystem, shell, export, or write tools", async () => {
    const writeCall = plannedCall("bash", { command: "touch forbidden" });
    const executeToolCall = vi.fn();
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "", toolCalls: [writeCall] });
    const result = await runPiTurn({
      messages: conversation, requestToolCompletion, executeToolCall,
      selectedToolset: readOnlyToolset(["get_ontology_catalog", "bash", "export_defect_records", "octane.defect.add_comment"]),
    });
    expect(result.selectedToolset.toolNames).toEqual(["get_ontology_catalog"]);
    expect(requestToolCompletion.mock.calls[0][0].tools.map((tool: { function: { name: string } }) => tool.function.name))
      .toEqual(["get_ontology_catalog"]);
    expect(executeToolCall).not.toHaveBeenCalled();
    expect(result.stoppedReason).toBe("tool_not_allowed");
    expect(result.evidence).toEqual([]);
  });

  it.each(["{", "[]", '{"unexpected":true}'])("rejects invalid arguments without executing: %s", async (args) => {
    const call = plannedCall("get_ontology_catalog");
    call.function.arguments = args;
    const executeToolCall = vi.fn();
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "", toolCalls: [call] });
    const result = await runPiTurn({ messages: conversation, requestToolCompletion, executeToolCall });
    expect(executeToolCall).not.toHaveBeenCalled();
    expect(requestToolCompletion).toHaveBeenCalledTimes(1);
    expect(result.stoppedReason).toBe("invalid_tool_arguments");
  });

  it("stops the remaining batch and follow-up planning when clarification is needed", async () => {
    const clarification = plannedCall("ask_clarification", { question: "Which date range?" });
    const laterCall = plannedCall("get_ontology_catalog");
    const executeToolCall = vi.fn().mockResolvedValue({ ...toolResult(clarification), requiresUserInput: true });
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "", toolCalls: [clarification, laterCall] });
    const result = await runPiTurn({ messages: conversation, requestToolCompletion, executeToolCall });
    expect(executeToolCall).toHaveBeenCalledTimes(1);
    expect(executeToolCall.mock.calls[0][0]).toEqual(clarification);
    expect(requestToolCompletion).toHaveBeenCalledTimes(1);
    expect(result.stoppedReason).toBe("clarification_requested");
  });

  it("does not retry a scope denial or execute the rest of its batch", async () => {
    const deniedCall = plannedCall("get_ontology_catalog");
    const laterCall = plannedCall("get_data_catalog");
    const executeToolCall = vi.fn().mockResolvedValue(toolResult(deniedCall, {
      ok: false, code: "ACTOR_SCOPE_DENIED", statusCode: 403, error: "Denied in this scope",
    }));
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "", toolCalls: [deniedCall, laterCall] });
    const result = await runPiTurn({ messages: conversation, requestToolCompletion, executeToolCall });
    expect(executeToolCall).toHaveBeenCalledTimes(1);
    expect(result.stoppedReason).toBe("tool_access_denied");
    expect(result.toolEvents).toContainEqual(expect.objectContaining({
      type: "tool-recovery", recovery: expect.objectContaining({ action: "deny" }),
    }));
  });

  it("enforces the model step limit", async () => {
    const call = plannedCall("get_ontology_catalog");
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "", toolCalls: [call] });
    const result = await runPiTurn({
      messages: conversation, requestToolCompletion, executeToolCall: vi.fn().mockResolvedValue(toolResult(call)), maxSteps: 1,
    });
    expect(requestToolCompletion).toHaveBeenCalledTimes(1);
    expect(result.stoppedReason).toBe("max_steps");
    expect(result.metrics).toMatchObject({ modelCalls: 1, maxSteps: 1 });
  });

  it("does not execute identical calls twice even when JSON key order differs", async () => {
    const first = plannedCall("search_analytics_filter_values", { dataset: "defects", field: "detected_by", query: "Synthetic" }, "first");
    const repeated = plannedCall("search_analytics_filter_values", { query: "Synthetic", field: "detected_by", dataset: "defects" }, "repeated");
    const executeToolCall = vi.fn().mockResolvedValue(toolResult(first));
    const requestToolCompletion = vi.fn().mockResolvedValue({ content: "", toolCalls: [first, repeated] });
    const result = await runPiTurn({ messages: conversation, requestToolCompletion, executeToolCall });
    expect(executeToolCall).toHaveBeenCalledTimes(1);
    expect(requestToolCompletion).toHaveBeenCalledTimes(1);
    expect(result.stoppedReason).toBe("repeated_tool_calls");
    expect(result.toolEvents).toContainEqual(expect.objectContaining({ type: "tool-blocked", reason: "repeated_tool_calls" }));
  });

  it("automatically diagnoses an empty analytics result through a Pi tool turn", async () => {
    const query = {
      dataset: "defects", intent: "aggregate", metrics: ["defect_count"], dimensions: [], filters: {},
      time: { field: "creation_time", current: ["2026-06-01", "2026-06-30"], timezone: "Asia/Shanghai" },
    };
    const call = plannedCall("query_analytics", query, "empty-query");
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValueOnce({ content: "No governed data is available.", toolCalls: [] });
    const executeToolCall = vi.fn(async (toolCall) => toolResult(toolCall, {
      ok: true, result: toolCall.function.name === "query_analytics"
        ? { rows: [], returned_groups: 0 } : { causeCode: "NO_SINGLE_FILTER_RECOVERY" },
    }));
    const result = await runPiTurn({ messages: conversation, requestToolCompletion, executeToolCall });
    expect(result.toolCalls.map((toolCall: ReturnType<typeof plannedCall>) => toolCall.function.name))
      .toEqual(["query_analytics", "diagnose_analytics_empty"]);
    expect(JSON.parse(executeToolCall.mock.calls[1][0].function.arguments).query).toEqual(query);
    expect(requestToolCompletion).toHaveBeenCalledTimes(2);
    expect(result.piEvents.filter((event: { type: string }) => event.type === "tool_execution_start")).toHaveLength(2);
  });

  it("reuses one bounded transient read retry without repeating model planning", async () => {
    const call = plannedCall("get_ontology_catalog");
    const requestToolCompletion = vi.fn()
      .mockResolvedValueOnce({ content: "", toolCalls: [call] })
      .mockResolvedValueOnce({ content: "Done.", toolCalls: [] });
    const executeToolCall = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error("Temporary failure"), { status: 503 }))
      .mockResolvedValueOnce(toolResult(call));
    const result = await runPiTurn({
      messages: conversation, requestToolCompletion, executeToolCall,
      toolDependencies: { toolRecoveryWait: vi.fn().mockResolvedValue(undefined), toolRecoveryRandom: () => 0 },
    });
    expect(executeToolCall).toHaveBeenCalledTimes(2);
    expect(requestToolCompletion).toHaveBeenCalledTimes(2);
    expect(result.toolEvents).toContainEqual(expect.objectContaining({
      type: "tool-recovery", recovery: expect.objectContaining({ action: "retry", outcome: "recovered" }),
    }));
  });

  it("returns a failed planning status without exposing the provider's error", async () => {
    const result = await runPiTurn({
      messages: conversation, requestToolCompletion: vi.fn().mockRejectedValue(new Error("Private upstream detail")),
      executeToolCall: vi.fn(),
    });
    expect(result.stoppedReason).toBe("model_error");
    expect(JSON.stringify(result)).not.toContain("Private upstream detail");
  });

  it("rejects unsupported attachments before model or tool access", async () => {
    const requestToolCompletion = vi.fn();
    await expect(runPiTurn({
      messages: [{ role: "user", content: [{ type: "image" }] }], requestToolCompletion,
    })).rejects.toThrow("text-only");
    expect(requestToolCompletion).not.toHaveBeenCalled();
  });

  it("runs the default CLI comparison with synthetic data and no provider requests", () => {
    const completed = spawnSync(process.execPath, ["scripts/probePiAgentRuntime.mjs"], { encoding: "utf8" });
    expect(completed.status, completed.stderr).toBe(0);
    const report = JSON.parse(completed.stdout);
    expect(report).toMatchObject({ dataSource: "synthetic", parity: true, networkRequests: 0 });
    expect(report.cases).toHaveLength(3);
    expect(report.cases.every((item: { parity: boolean }) => item.parity)).toBe(true);
    expect(report.cases.map((item: { caseId: string }) => item.caseId)).toContain("execution-003-empty-alias-recovery");
  });

  it("returns a nonzero process status when synthetic parity fails", () => {
    const preload = `
      const parse = JSON.parse;
      JSON.parse = (text, reviver) => {
        const result = parse(text, reviver);
        if (result?.caseId === 'execution-001-semantic-citation') result.expected.toolSequence = ['mismatched_tool'];
        return result;
      };
    `;
    const completed = spawnSync(process.execPath, [
      "--import", `data:text/javascript,${encodeURIComponent(preload)}`, "scripts/probePiAgentRuntime.mjs",
    ], { encoding: "utf8" });
    expect(completed.status, completed.stderr).toBe(1);
    expect(JSON.parse(completed.stdout)).toMatchObject({ parity: false, dataSource: "synthetic" });
  });

  it.each(["get_ontology_catalog", "diagnose_analytics_empty"])(
    "finishes immediate alias recovery before the remaining planned %s call", async (toolName) => {
      const cases = fs.readFileSync("evals/main-agent/target/execution-golden.jsonl", "utf8")
        .trim().split("\n").map((line) => JSON.parse(line));
      const item = cases.find((entry) => entry.caseId === "execution-003-empty-alias-recovery");
      item.plans[0].push({
        id: "later-planned-call", name: toolName,
        arguments: toolName === "diagnose_analytics_empty" ? { query: item.plans[0][0].arguments } : {},
      });
      const { runPiRuntimeProbe } = await import("../../../../scripts/probePiAgentRuntime.mjs");
      const report = await runPiRuntimeProbe({ cases: [item] });
      expect(report.cases[0].pi.toolSequence).toEqual(["query_analytics", "diagnose_analytics_empty", "query_analytics"]);
      expect(report.parity).toBe(true);
    },
  );

  it("detects altered results, evidence, and UI events despite unchanged counts", async () => {
    const probe = await import("../../../../scripts/probePiAgentRuntime.mjs");
    expect(probe).toHaveProperty("fingerprintToolTurnContract", expect.any(Function));
    const fingerprint = Reflect.get(probe, "fingerprintToolTurnContract");
    const result = {
      toolCalls: [plannedCall("get_ontology_catalog")],
      toolMessages: [toolResult(plannedCall("get_ontology_catalog"), { ok: true, count: 3 }).toolMessage],
      evidence: [{ tool: "get_ontology_catalog", summary: { count: 3 } }],
      contextText: "Synthetic count: 3", toolEvents: [{ type: "tool-output-available", outputSummary: "Count: 3" }],
    };
    const baseline = fingerprint(result);
    expect(fingerprint({ ...result, toolMessages: [toolResult(plannedCall("get_ontology_catalog"), { ok: true, count: 9 }).toolMessage] })).not.toBe(baseline);
    expect(fingerprint({ ...result, evidence: [{ tool: "get_ontology_catalog", summary: { count: 9 } }] })).not.toBe(baseline);
    expect(fingerprint({ ...result, toolEvents: [] })).not.toBe(baseline);
  });
});