import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import duplicateBridgeRuntime from "./duplicateBridgeRuntime.cjs";
import { streamAgentChatResponse } from "./agentRuntime/langGraphChatHandler.mjs";
import { resolveAgentRuntimeMode } from "./agentRuntime/governedChatRuntime.mjs";
import { createPiChatRuntime } from "./agentRuntime/piChatRuntime.mjs";
import { createBackendToolDiscovery } from "./mainAgentToolRegistry.mjs";
import { createFileAgentRuntimeStore } from "./agentRuntime/runtimeAuditStore.mjs";
import { resolveAgentOperationsResponse } from "./agentOperations.mjs";
import { createDuplicateWarmupManager } from "./duplicateWarmup.mjs";
import { extractLatestUserQuery, resolveAiDefectContext } from "./aiContext.mjs";
import { writeSseEvent } from "./companyChat.mjs";
import { createLocalHttpBoundary } from "./localHttpBoundary.mjs";
import { resolveRequestUrl } from "./httpRequestUrl.mjs";
import { proxyAnalyticsDownload } from "./analyticsDownloadProxy.mjs";
import { resolveInternalAuxiliaryActor, runAuthenticatedChatRequest, toSafeAgentAuthResponse } from "./agentAuth.mjs";
import { attachDuplicateSummary } from "./duplicateResultEnrichment.mjs";
import { loadLocalEnv } from "./loadLocalEnv.mjs";
import {
  defaultQGateReportsRoot,
  findLatestQGateDashboardReport,
  resolveQGateDashboardHtmlPath,
} from "./qgateReports.mjs";
import { handleTranscribeRequest } from "./transcribe.mjs";
import { resolveChatModelConfig } from "./chatModelConfig.mjs";
import testcaseBridgeRuntime from "./testcaseBridgeRuntime.cjs";
import { buildFilterDefectBridgePayload } from "./testcaseFilterRequest.mjs";
import { resolveTestcaseBatchConfig } from "./testcaseBatchConfig.mjs";
import {
  buildPatternCoverageAuditBridgePayload,
  buildPatternDiscoveryBridgePayload,
  buildReusablePatternPreparationBridgePayload,
} from "./testcasePatternDiscoveryRequest.mjs";
import {
  buildReusablePatternDraftExportPayload,
  buildReusablePatternPromptContext,
  buildReusablePatternVerificationPayload,
} from "./testcasePatternGeneration.mjs";

const { runDuplicateBridge, stopDuplicateBridgeRuntime } = duplicateBridgeRuntime;
const { runTestCaseBridge, stopTestCaseBridgeRuntime } = testcaseBridgeRuntime;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..");
loadLocalEnv();
const port = Number(process.env.VIZION_API_PORT || 3004);
const localHttpBoundary = createLocalHttpBoundary(process.env);
const staticDir = fs.existsSync(path.join(repoRoot, "dist")) ? path.join(repoRoot, "dist") : "";
const duplicateWarmupManager = createDuplicateWarmupManager({
  runDuplicateBridge,
  logger: console,
});
const agentRuntimeStore = createFileAgentRuntimeStore();
const configuredRuntimeMode = resolveAgentRuntimeMode(process.env);
const chatRuntime = configuredRuntimeMode === "langgraph"
  ? (await import("./agentRuntime/langGraphChatRuntime.mjs")).createLangGraphChatRuntime({ runtimeStore: agentRuntimeStore })
  : createPiChatRuntime({ runtimeStore: agentRuntimeStore, discoverTools: createBackendToolDiscovery() });

function nowMs() {
  return performance.now();
}

function roundMs(value) {
  return Number(value.toFixed(1));
}

function buildRequestId(prefix) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function summarizeQuery(queryText) {
  const normalized = String(queryText || "").replace(/\s+/g, " ").trim();
  return {
    length: normalized.length,
    preview: normalized.slice(0, 80),
  };
}

function logMetric(event, payload) {
  console.info(`[vizion-metric] ${JSON.stringify({ event, ...payload })}`);
}

export async function handleAiChatRequest(request, response) {
  return runAuthenticatedChatRequest(request, {
    env: process.env,
    readBody: () => readJsonBody(request),
    runChat: (body) => handleAuthenticatedAiChatRequest(body, response),
    sendAuthResponse: (authResponse) => sendJson(response, authResponse.statusCode, authResponse.payload),
    sendBadRequestResponse: (badRequestResponse) => sendJson(
      response,
      badRequestResponse.statusCode,
      badRequestResponse.payload,
    ),
  });
}

async function requireInternalAuxiliaryActor(request, response) {
  try {
    await resolveInternalAuxiliaryActor(request, { env: process.env });
    return true;
  } catch (error) {
    const safe = toSafeAgentAuthResponse(error);
    sendJson(response, safe?.statusCode || 503, safe?.payload || { success: false, error: "AUXILIARY_ROUTE_UNAVAILABLE" });
    return false;
  }
}

async function handleAuthenticatedAiChatRequest(body, response) {
  const startedAt = nowMs();
  const requestId = buildRequestId("ai-chat");
  const queryText = extractLatestUserQuery(body?.messages);
  const runtimeMode = chatRuntime.kind;
  const useDefectContext = body?.useDefectContext === true;
  const useAnalyticsContext = body?.useAnalyticsContext === true;
  let runtimeResult = null;
  let streamMetrics = null;

  try {
      const completed = await streamAgentChatResponse({
        body,
        response,
        runtime: chatRuntime,
        toolDependencies: {
          runDuplicateBridge,
          ensureDuplicateWarmup: () => duplicateWarmupManager.ensureWarm({ reason: `${runtimeMode}-agent-tool` }),
        },
        onCompleted: async (completed) => {
          await agentRuntimeStore.appendRunEvent({
            runId: completed.runtimeResult?.runId || "",
            threadId: completed.runtimeResult?.threadId || "",
            actorScope: completed.runtimeResult?.actorScope || {},
            type: "agent-stream-completed",
            ...(Number.isFinite(Number(completed.streamMetrics?.streamTotalMs)) ? { latencyMs: Number(completed.streamMetrics.streamTotalMs) } : {}),
            ...(completed.streamMetrics?.tokenUsage ? { tokenUsage: completed.streamMetrics.tokenUsage } : {}),
            citationValidation: completed.answerValidation
              ? completed.answerValidation.valid ? "pass" : "blocked"
              : "not_required",
            evidenceGateStatus: completed.runtimeResult?.mainAgentToolContext?.evidenceGate?.status || "not_required",
            evidenceGateViolations: completed.runtimeResult?.mainAgentToolContext?.evidenceGate?.violations || [],
            answerValidationViolations: completed.answerValidation?.violations || [],
          });
        },
      });
      runtimeResult = completed.runtimeResult;
      streamMetrics = completed.streamMetrics;
      logMetric("ai_chat_request", {
        requestId,
        runtime: runtimeMode,
        model: String(body?.model || ""),
        query: summarizeQuery(runtimeResult?.queryText || queryText),
        aiContextEnabled: runtimeResult?.metrics?.aiContextEnabled || false,
        analyticsContextEnabled: runtimeResult?.metrics?.analyticsContextEnabled || false,
        mainAgentToolCallCount: runtimeResult?.metrics?.mainAgentToolCallCount || 0,
        aiContextTimings: runtimeResult?.metrics?.aiContextTimings || null,
        streamMetrics,
        totalMs: roundMs(nowMs() - startedAt),
      });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown server error";
    logMetric("ai_chat_request_failed", {
      requestId,
      runtime: runtimeMode,
      model: String(body?.model || ""),
      query: summarizeQuery(runtimeResult?.queryText || queryText),
      aiContextEnabled: useDefectContext,
      analyticsContextEnabled: useAnalyticsContext,
      mainAgentToolCallCount: runtimeResult?.metrics?.mainAgentToolCallCount || 0,
      aiContextTimings: runtimeResult?.metrics?.aiContextTimings || null,
      streamMetrics,
      totalMs: roundMs(nowMs() - startedAt),
      error: message,
    });
    if (response.headersSent) {
      writeSseEvent(response, { type: "error", message });
      response.write("data: [DONE]\n\n");
      response.end();
      return;
    }
    throw error;
  }
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
  });
  response.end(JSON.stringify(payload));
}

async function readJsonBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }

  const raw = Buffer.concat(chunks).toString("utf8").trim();
  return raw ? JSON.parse(raw) : {};
}

async function proxyAnalyticsJson(pathname, searchParams, response) {
  const analyticsUrl = new URL(pathname, "http://127.0.0.1:3003");
  searchParams.forEach((value, key) => analyticsUrl.searchParams.append(key, value));
  const analyticsResponse = await fetch(analyticsUrl);
  const payload = await analyticsResponse.text();
  response.writeHead(analyticsResponse.status, {
    "Content-Type": analyticsResponse.headers.get("content-type") || "application/json; charset=utf-8",
  });
  response.end(payload);
}

/**
 * Run an async mapper over items with a bounded concurrency limit.
 * Preserves input order in the output array. A mapper that throws is caught
 * and its slot is filled with `null` (caller decides how to handle).
 */
async function mapWithConcurrency(items, concurrency, mapper) {
  const results = new Array(items.length).fill(null);
  let nextIndex = 0;
  async function worker() {
    while (true) {
      const i = nextIndex++;
      if (i >= items.length) return;
      results[i] = await mapper(items[i], i);
    }
  }
  const workers = [];
  const effectiveConcurrency = Math.max(1, Math.min(concurrency, items.length));
  for (let w = 0; w < effectiveConcurrency; w++) {
    workers.push(worker());
  }
  await Promise.all(workers);
  return results;
}

async function generateTestCaseContent(defectInfo, fewShotText, chatConfig, intent = "", reusablePatternContext = null) {
  const { buildChatCompletionRequest } = await import("./chatModelConfig.mjs");

  if (!chatConfig.credential) {
    throw new Error("LLM credentials not configured — set DUPSEARCH_CHAT_ACCESS_CODE or DUPSEARCH_CHAT_API_KEY");
  }

  const reusablePatternPrompt = reusablePatternContext
    ? buildReusablePatternPromptContext(reusablePatternContext)
    : null;
  const systemPrompt = [
    "You are a test case generator for BMW ALM Octane (workspace 1002/2001).",
    "Generate a structured manual test case (test_manual) from a defect's reproduction information.",
    "",
    "Output format — return a JSON object with exactly these fields:",
    '{"testName": "[project][SW] Regression - short summary (D<defect_id>)", "descriptionHtml": "<html><body>...</body></html>", "stepsText": "- [PreCon] ...\\n- action step\\n- ? checkpoint"}',
    "",
    "Description HTML sections: Objective, Reference (defect link), Preconditions, baseline/regression tables (if quantitative data), pass/fail criteria.",
    "Steps text format: one step per line, prefixed by kind: '- [PreCon] <text>' (precondition), '- <text>' (action step), '- ? <text>' (checkpoint/expected result).",
    "Do NOT put the procedure in the description — it goes in stepsText only.",
    "Checkpoints must have specific values/thresholds (numbers, percentages), not vague 'check it works'.",
    reusablePatternPrompt?.systemInstructions,
  ].filter(Boolean).join("\n");

  const userPrompt = [
    `Defect ID: ${defectInfo.defect_id}`,
    `Name: ${defectInfo.name}`,
    `Severity: ${defectInfo.severity}`,
    `Software: ${defectInfo.software_version}`,
    `ECU: ${defectInfo.assigned_ecu}`,
    `Lead model: ${defectInfo.lead_model}`,
    `Project: ${defectInfo.project}`,
    "",
    "Defect description:",
    defectInfo.description?.slice(0, 3000) || "(no description)",
    "",
    reusablePatternPrompt?.userContext || "",
    "",
    fewShotText ? `Reference test cases (style/structure examples):\n${fewShotText}\n` : "",
    intent ? `User intent / requirements (tailor the test case to these):\n${intent}\n` : "",
    "Generate the test case now. Return ONLY the JSON object, no markdown code fence.",
  ].join("\n");

  const requestConfig = buildChatCompletionRequest({
    selectedModel: chatConfig.model,
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user", content: userPrompt },
    ],
    env: process.env,
    // Test-case generation outputs a full HTML description + structured steps as JSON,
    // which needs more tokens than the 900-token default used by chat / summary flows.
    // 8192 to avoid truncation of large descriptionHtml + stepsText payloads.
    maxTokens: 8192,
  });

  const abortController = new AbortController();
  const timeout = setTimeout(() => abortController.abort(), 120000);
  let response;
  try {
    response = await fetch(requestConfig.url, {
      method: "POST",
      headers: requestConfig.headers,
      body: JSON.stringify(requestConfig.body),
      signal: abortController.signal,
    });
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    throw new Error(`LLM request failed: ${response.status} ${response.statusText}`);
  }

  const data = await response.json();
  const content = data?.choices?.[0]?.message?.content || "";
  if (!content.trim()) {
    const finishReason = data?.choices?.[0]?.finish_reason || "unknown";
    throw new Error(`LLM returned empty content (finish_reason: ${finishReason})`);
  }
  // Parse JSON from the LLM response (it may be wrapped in markdown code fences)
  const jsonMatch = content.match(/\{[\s\S]*\}/);
  if (!jsonMatch) {
    // Log the raw content to help diagnose non-JSON responses
    console.error("[testcase-gen] LLM did not return JSON. Raw content (first 500 chars):", content.slice(0, 500));
    throw new Error("LLM did not return a JSON object");
  }
  let parsed;
  try {
    parsed = JSON.parse(jsonMatch[0]);
  } catch (parseErr) {
    // LLMs sometimes produce malformed JSON — unescaped newlines inside string
    // values, trailing commas, or smart quotes. Try to repair common issues before
    // giving up. This is NOT a general JSON parser — it handles the specific
    // patterns we've seen from glm-5 / deepseek / qwen.
    console.warn("[testcase-gen] JSON.parse failed, attempting repair. Error:", parseErr.message);
    let repaired = jsonMatch[0];
    // Replace smart quotes with straight quotes.
    repaired = repaired.replace(/[“”]/g, '"').replace(/[‘’]/g, "'");
    // Remove markdown code fence remnants inside the JSON.
    repaired = repaired.replace(/```json\s*/g, "").replace(/```\s*/g, "");
    // Remove trailing commas before } or ].
    repaired = repaired.replace(/,\s*([}\]])/g, "$1");
    try {
      parsed = JSON.parse(repaired);
    } catch (repairErr) {
      console.error("[testcase-gen] repair also failed. Content length:", jsonMatch[0].length, "Error:", repairErr.message);
      console.error("[testcase-gen] Last 200 chars of matched content:", jsonMatch[0].slice(-200));
      throw new Error("LLM returned malformed JSON that could not be repaired (likely unescaped content in descriptionHtml)");
    }
  }

  return {
    testName: String(parsed.testName || `[${defectInfo.project}] Regression - ${defectInfo.name?.slice(0, 60)} (D${defectInfo.defect_id})`),
    descriptionHtml: String(parsed.descriptionHtml || ""),
    stepsText: String(parsed.stepsText || ""),
  };
}

/**
 * Use LLM to understand the user's natural-language intent for test-case creation.
 *
 * Given a free-text input like "基于历史的RSU的缺陷创建测试用例" or "蓝牙连接断开后的重连测试",
 * returns a structured decision:
 *   - { mode: "filter_defects", keyword: "RSU" }
 *       The user wants to find ALL existing defects matching a topic, then generate
 *       test cases from them. `keyword` is the concise filter term (project code,
 *       ECU name, function name) to match against the defect DB.
 *   - { mode: "direct_generate", description: "蓝牙连接断开后的重连测试", ... }
 *       The user describes a test scenario directly — no existing defect to look
 *       up. Generate a test case from the description alone.
 *
 * This replaces brittle regex keyword extraction — the LLM understands that
 * "基于历史的RSU的缺陷" means "filter defects by RSU", and "测试蓝牙重连" means
 * "generate a test from this description".
 */
async function analyzeTestcaseIntent(userInput, chatConfig) {
  const { buildChatCompletionRequest } = await import("./chatModelConfig.mjs");

  if (!chatConfig.credential) {
    throw new Error("LLM credentials not configured — set DUPSEARCH_CHAT_ACCESS_CODE or DUPSEARCH_CHAT_API_KEY");
  }

  const systemPrompt = [
    "You are an intent analyzer for a BMW test-case creation tool.",
    "Given the user's natural-language request, decide what they want:",
    "",
    "1. filter_defects — the user wants to find EXISTING defects matching a topic/keyword",
    "   (e.g. project code like RSU, RSE, ADAS, IDCEVO; or a function/ECU name), then generate",
    "   test cases from those defects. Extract the concise filter keyword.",
    "   Signals: mentions '历史缺陷', '相关缺陷', '所有XX', '基于XX的缺陷', a project/ECU code.",
    "",
    "2. direct_generate — the user directly describes a test scenario or requirement",
    "   without referencing existing defects. Generate a test case from the description.",
    "   Signals: describes a test procedure, a feature to test, a scenario — no mention of",
    "   searching existing defects.",
    "",
    "Return a JSON object with exactly these fields:",
    '{"mode": "filter_defects" | "direct_generate", "keyword": "<concise filter term, or empty>", "description": "<normalized test description, or empty>", "reasoning": "<one sentence>"}',
    "",
    "For filter_defects: keyword should be a short term likely to match the defect DB's",
    "project or name column (e.g. RSU, RSE, ADAS, IDCEVO, DTSV, App, 蓝牙, NTF, FIS).",
    "For direct_generate: description should be the cleaned-up test scenario description.",
    "Return ONLY the JSON object, no markdown code fence.",
  ].join("\n");

  const userPrompt = `User input: ${userInput}\n\nAnalyze the intent and return the JSON object.`;

  const requestConfig = buildChatCompletionRequest({
    selectedModel: chatConfig.model,
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user", content: userPrompt },
    ],
    env: process.env,
    maxTokens: 512,
  });

  const abortController = new AbortController();
  const timeout = setTimeout(() => abortController.abort(), 30000);
  let response;
  try {
    response = await fetch(requestConfig.url, {
      method: "POST",
      headers: requestConfig.headers,
      body: JSON.stringify(requestConfig.body),
      signal: abortController.signal,
    });
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    throw new Error(`LLM intent analysis failed: ${response.status} ${response.statusText}`);
  }

  const data = await response.json();
  const content = data?.choices?.[0]?.message?.content || "";
  const jsonMatch = content.match(/\{[\s\S]*\}/);
  if (!jsonMatch) {
    throw new Error("LLM intent analysis did not return JSON");
  }

  let parsed;
  try {
    parsed = JSON.parse(jsonMatch[0]);
  } catch {
    throw new Error("LLM intent analysis returned malformed JSON");
  }

  return {
    mode: String(parsed.mode || "filter_defects"),
    keyword: String(parsed.keyword || "").trim(),
    description: String(parsed.description || "").trim(),
    reasoning: String(parsed.reasoning || ""),
  };
}

function serveStaticAsset(request, response, url) {
  if (!staticDir || request.method !== "GET") {
    return false;
  }

  const rawPath = url.pathname === "/" ? "/index.html" : url.pathname;
  const assetPath = path.normalize(path.join(staticDir, rawPath));
  if (!assetPath.startsWith(staticDir) || !fs.existsSync(assetPath) || fs.statSync(assetPath).isDirectory()) {
    const indexPath = path.join(staticDir, "index.html");
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(fs.readFileSync(indexPath));
    return true;
  }

  const ext = path.extname(assetPath).toLowerCase();
  const contentTypes = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
  };

  response.writeHead(200, {
    "Content-Type": contentTypes[ext] || "application/octet-stream",
  });
  response.end(fs.readFileSync(assetPath));
  return true;
}

const server = http.createServer(async (request, response) => {
  const admission = localHttpBoundary.evaluate({ method: request.method, headers: request.headers });
  if (!admission.allowed) {
    sendJson(response, admission.statusCode, { success: false, error: admission.code });
    return;
  }
  if (admission.corsOrigin) {
    response.setHeader("Access-Control-Allow-Origin", admission.corsOrigin);
  }

  const url = resolveRequestUrl(request.url, request.headers.host);

  if (request.method === "OPTIONS") {
    response.writeHead(204, {
      ...(admission.corsOrigin ? { "Access-Control-Allow-Origin": admission.corsOrigin } : {}),
      "Access-Control-Allow-Headers": "content-type, authorization",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    });
    response.end();
    return;
  }

  try {
    if (request.method === "GET" && url.pathname === "/health") {
      sendJson(response, 200, { ok: true, service: "vizion-local-api", agentRuntime: chatRuntime.kind });
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/agent-operations/summary") {
      const result = await resolveAgentOperationsResponse({
        request,
        operation: "summary",
        env: process.env,
        rootDir: agentRuntimeStore.rootDir,
      });
      sendJson(response, result.statusCode, result.payload);
      return;
    }

    const operationRunMatch = request.method === "GET" && /^\/api\/agent-operations\/runs\/([^/]+)$/.exec(url.pathname);
    if (operationRunMatch) {
      const result = await resolveAgentOperationsResponse({
        request,
        operation: "run",
        runId: decodeURIComponent(operationRunMatch[1]),
        env: process.env,
        rootDir: agentRuntimeStore.rootDir,
      });
      sendJson(response, result.statusCode, result.payload);
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/qgate-reports/latest-dashboard") {
      sendJson(response, 200, findLatestQGateDashboardReport(defaultQGateReportsRoot));
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/agent/analytics/defects/export/download") {
      await proxyAnalyticsDownload({
        pathname: url.pathname,
        searchParams: url.searchParams,
        response,
      });
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/qgate-reports/weekly-report") {
      await proxyAnalyticsJson(url.pathname, url.searchParams, response);
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/qgate-reports/dashboard-html") {
      const filePath = resolveQGateDashboardHtmlPath(
        defaultQGateReportsRoot,
        url.searchParams.get("run"),
        url.searchParams.get("file"),
      );
      response.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
      });
      response.end(fs.readFileSync(filePath));
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/ai/context") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const startedAt = nowMs();
      const requestId = buildRequestId("ai-context");
      const body = await readJsonBody(request);
      const aiContext = await resolveAiDefectContext({
        runDuplicateBridge,
        ensureDuplicateWarmup: () => duplicateWarmupManager.ensureWarm({ reason: "ai-context-endpoint" }),
        messages: body?.messages,
        topK: 5,
      });
      logMetric("ai_context_request", {
        requestId,
        query: summarizeQuery(aiContext.queryText),
        timings: aiContext.timings,
        totalMs: roundMs(nowMs() - startedAt),
      });
      sendJson(response, 200, {
        success: true,
        queryText: aiContext.queryText,
        context: aiContext.contextText,
        result: aiContext.duplicateSearchResult,
        timings: aiContext.timings,
      });
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/duplicate-search/warmup-status") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      sendJson(response, 200, duplicateWarmupManager.getStatus());
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/duplicate-search/warmup") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const status = await duplicateWarmupManager.ensureWarm({
        reason: typeof body?.reason === "string" && body.reason.trim() ? body.reason.trim() : "manual",
      });
      sendJson(response, 200, status);
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/ai/chat") {
      await handleAiChatRequest(request, response);
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/ai/transcribe") {
      const body = await readJsonBody(request);
      const result = await handleTranscribeRequest(body);
      sendJson(response, 200, result);
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/chat") {
      await handleAiChatRequest(request, response);
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/duplicate-search") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const query = typeof body?.query === "string" ? body.query.trim() : "";
      const selectedModel = typeof body?.model === "string" ? body.model.trim() : "";
      const topKRaw = Number(body?.top_k ?? 8);
      const topK = Number.isFinite(topKRaw) ? Math.max(1, Math.min(20, Math.floor(topKRaw))) : 8;

      if (!query) {
        sendJson(response, 400, { success: false, error: "query is required" });
        return;
      }

      const warmupStatus = duplicateWarmupManager.getStatus();
      if (warmupStatus.state === "warming") {
        stopDuplicateBridgeRuntime();
      }

      const result = await runDuplicateBridge({
        action: "search",
        query,
        top_k: topK,
      });

      if (result?.success && result?.result) {
        result.result = await attachDuplicateSummary({
          query,
          selectedModel,
          result: result.result,
        });
      }

      sendJson(response, result?.success ? 200 : 500, result);
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/duplicate-feedback") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const queryText = typeof body?.queryText === "string" ? body.queryText.trim() : "";
      const ticketId = typeof body?.ticketId === "string" ? body.ticketId.trim() : "";
      const signal = typeof body?.signal === "string" ? body.signal.trim().toLowerCase() : "";

      if (!queryText || !ticketId || !signal) {
        sendJson(response, 400, {
          success: false,
          error: "queryText/ticketId/signal are required",
        });
        return;
      }

      const result = await runDuplicateBridge({
        action: "feedback",
        query_text: queryText,
        ticket_id: ticketId,
        signal,
        base_score: body?.baseScore,
        rank_pos: body?.rankPos,
        user_id: body?.userId,
      });

      sendJson(response, result?.success ? 200 : 500, result);
      return;
    }

    // --- Create Test Case (slash command /create-testcase) ---

    if (request.method === "POST" && url.pathname === "/api/create-testcase/warmup") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      try {
        const result = await runTestCaseBridge({ action: "warmup" });
        sendJson(response, result?.success ? 200 : 500, result);
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const defectId = typeof body?.defect_id === "string" ? body.defect_id.trim() : "";
      const intent = typeof body?.intent === "string" ? body.intent.trim() : "";

      if (!defectId) {
        sendJson(response, 400, { success: false, error: "defect_id is required" });
        return;
      }

      try {
        // Step 1: Python bridge — read defect + RAG retrieve similar test cases
        const prepared = await runTestCaseBridge({ action: "prepare", defect_id: defectId });
        if (!prepared?.success || !prepared?.defect_info) {
          sendJson(response, 500, { success: false, error: prepared?.error || "failed to prepare defect" });
          return;
        }

        const defectInfo = prepared.defect_info;
        const similarCases = prepared.similar_cases || [];
        const fewShotText = prepared.few_shot_text || "";

        // Step 2: LLM generation — build description HTML + steps text
        const chatConfig = resolveChatModelConfig(undefined, process.env);
        const llmResult = await generateTestCaseContent(defectInfo, fewShotText, chatConfig, intent);

        // Step 3: Python bridge — verify quality
        const verified = await runTestCaseBridge({
          action: "verify",
          defect_info: defectInfo,
          description_html: llmResult.descriptionHtml,
          steps_text: llmResult.stepsText,
        });

        const result = {
          defectId: defectInfo.defect_id,
          defectName: defectInfo.name,
          defectSeverity: defectInfo.severity,
          defectSoftwareVersion: defectInfo.software_version,
          defectAssignedEcu: defectInfo.assigned_ecu,
          defectLeadModel: defectInfo.lead_model,
          descriptionHtml: llmResult.descriptionHtml,
          stepsText: llmResult.stepsText,
          name: llmResult.testName,
          verification: verified?.verification || { passed: false, criteria: [], feedback: "verification skipped" },
          similarCases,
          generatedAt: new Date().toISOString(),
        };

        sendJson(response, 200, { success: true, result });
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase/verify") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      try {
        const result = await runTestCaseBridge({
          action: "verify",
          defect_info: body?.defect_info || {},
          description_html: body?.description_html || "",
          steps_text: body?.steps_text || "",
        });
        sendJson(response, result?.success ? 200 : 500, result);
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase/commit") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const testCaseData = body?.test_case_data;
      const featureId = typeof body?.feature_id === "string" ? body.feature_id.trim() : "";
      const ownerId = typeof body?.owner_workspace_user_id === "string" ? body.owner_workspace_user_id.trim() : "";

      if (!testCaseData) {
        sendJson(response, 400, { success: false, error: "test_case_data is required" });
        return;
      }

      try {
        const result = await runTestCaseBridge({
          action: "commit",
          test_case_data: { ...testCaseData, owner_workspace_user_id: ownerId },
          feature_id: featureId,
        });
        sendJson(response, result?.success ? 200 : 500, result);
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    // --- Batch test-case creation: semantic search + batch prepare + batch commit ---

    // Analyze the user's natural-language intent for test-case creation.
    // Returns { mode: "filter_defects"|"direct_generate", keyword, description }.
    if (request.method === "POST" && url.pathname === "/api/create-testcase/analyze-intent") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const userInput = typeof body?.input === "string" ? body.input.trim() : "";

      if (!userInput) {
        sendJson(response, 400, { success: false, error: "input is required" });
        return;
      }

      try {
        const chatConfig = resolveChatModelConfig(undefined, process.env);
        const result = await analyzeTestcaseIntent(userInput, chatConfig);
        sendJson(response, 200, { success: true, ...result });
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase/search") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const query = typeof body?.query === "string" ? body.query.trim() : "";
      const topK = Number(body?.top_k) || 10;

      if (!query) {
        sendJson(response, 400, { success: false, error: "query is required" });
        return;
      }

      try {
        const result = await runTestCaseBridge({ action: "search_defects", query, top_k: topK });
        sendJson(response, result?.success ? 200 : 500, result);
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    // Filter defects by project/name keyword, excluding defects already linked
    // to a test case. Returns ALL matches (paginated), unlike /search (similarity top-K).
    // Used when the user says "RSU 相关的所有缺陷" — topic-filter, not similarity-rank.
    if (request.method === "POST" && url.pathname === "/api/create-testcase/filter-defects") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const keyword = typeof body?.keyword === "string" ? body.keyword.trim() : "";
      const project = typeof body?.project === "string" ? body.project.trim() : "";
      const limit = Number(body?.limit) || 50;
      const offset = Number(body?.offset) || 0;

      if (!keyword) {
        sendJson(response, 400, { success: false, error: "keyword is required" });
        return;
      }

      try {
        const result = await runTestCaseBridge(buildFilterDefectBridgePayload({
          keyword,
          project,
          limit,
          offset,
          idsOnly: body?.ids_only === true,
        }));
        sendJson(response, result?.success ? 200 : 500, result);
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase/discover-patterns") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const keyword = typeof body?.keyword === "string" ? body.keyword.trim() : "";
      const project = typeof body?.project === "string" ? body.project.trim() : "";
      const defectIds = Array.isArray(body?.defect_ids)
        ? body.defect_ids.map((id) => String(id || "").trim()).filter(Boolean)
        : undefined;

      if (!keyword) {
        sendJson(response, 400, { success: false, error: "keyword is required" });
        return;
      }

      try {
        const result = await runTestCaseBridge(buildPatternDiscoveryBridgePayload({
          keyword,
          project,
          defectIds,
          similarityThreshold: body?.similarity_threshold,
        }));
        sendJson(response, result?.success ? 200 : 500, result);
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase/audit-pattern-coverage") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const runId = typeof body?.run_id === "string" ? body.run_id.trim() : "";
      const patternIds = Array.isArray(body?.pattern_ids)
        ? body.pattern_ids.map((id) => String(id || "").trim()).filter(Boolean)
        : undefined;

      if (!runId) {
        sendJson(response, 400, { success: false, error: "run_id is required" });
        return;
      }
      if (Array.isArray(body?.pattern_ids) && !patternIds?.length) {
        sendJson(response, 400, { success: false, error: "pattern_ids must contain at least one pattern ID" });
        return;
      }

      try {
        const result = await runTestCaseBridge(buildPatternCoverageAuditBridgePayload({ runId, patternIds }));
        sendJson(response, result?.success ? 200 : 500, result);
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase/generate-pattern-drafts") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const runId = typeof body?.run_id === "string" ? body.run_id.trim() : "";
      const patternIds = Array.isArray(body?.pattern_ids)
        ? body.pattern_ids.map((id) => String(id || "").trim()).filter(Boolean)
        : [];
      const intent = typeof body?.intent === "string" ? body.intent.trim() : "";
      const skipExport = Boolean(body?.skip_export);

      if (!runId) {
        sendJson(response, 400, { success: false, error: "run_id is required" });
        return;
      }
      if (patternIds.length === 0) {
        sendJson(response, 400, { success: false, error: "pattern_ids must contain at least one pattern ID" });
        return;
      }

      try {
        const prepared = await runTestCaseBridge(buildReusablePatternPreparationBridgePayload({ runId, patternIds }));
        if (!prepared?.success || !Array.isArray(prepared.contexts)) {
          sendJson(response, 500, { success: false, error: prepared?.error || "failed to prepare reusable testcase patterns" });
          return;
        }

        const chatConfig = resolveChatModelConfig(undefined, process.env);
        const { concurrency } = resolveTestcaseBatchConfig();
        const results = await mapWithConcurrency(prepared.contexts, concurrency, async (context) => {
          const patternId = String(context?.patternId || "");
          const defectInfo = context?.defectInfo || {};
          try {
            if (context?.error || !defectInfo?.defect_id) {
              throw new Error(context?.error || "pattern representative is unavailable in the local snapshot");
            }
            const llmResult = await generateTestCaseContent(
              defectInfo,
              String(context?.fewShotText || ""),
              chatConfig,
              intent,
              context,
            );
            const verified = await runTestCaseBridge(buildReusablePatternVerificationPayload({
              defectInfo,
              patternContext: context,
              generated: llmResult,
            }));
            return {
              patternId,
              representativeDefectId: defectInfo.defect_id,
              memberDefectIds: Array.isArray(context?.memberDefectIds) ? context.memberDefectIds : [],
              memberCount: Number(context?.memberCount || 0),
              commonAnchorTerms: Array.isArray(context?.commonAnchorTerms) ? context.commonAnchorTerms : [],
              parameterMatrix: context?.parameterMatrix || {},
              name: llmResult.testName,
              descriptionHtml: llmResult.descriptionHtml,
              stepsText: llmResult.stepsText,
              verification: verified?.verification || { passed: false, criteria: [], feedback: "verification skipped" },
              similarCases: context?.similarCases || [],
              generatedAt: new Date().toISOString(),
              error: null,
            };
          } catch (error) {
            return {
              patternId,
              representativeDefectId: String(defectInfo?.defect_id || ""),
              memberDefectIds: Array.isArray(context?.memberDefectIds) ? context.memberDefectIds : [],
              memberCount: Number(context?.memberCount || 0),
              commonAnchorTerms: Array.isArray(context?.commonAnchorTerms) ? context.commonAnchorTerms : [],
              parameterMatrix: context?.parameterMatrix || {},
              name: "",
              descriptionHtml: "",
              stepsText: "",
              verification: { passed: false, criteria: [], feedback: "" },
              similarCases: context?.similarCases || [],
              generatedAt: new Date().toISOString(),
              error: String(error?.message || error),
            };
          }
        });
        const succeeded = results.filter((result) => !result.error).length;
        let artifacts;
        let artifactError = "";
        if (!skipExport) {
          try {
            const exported = await runTestCaseBridge(buildReusablePatternDraftExportPayload({ runId, drafts: results }));
            if (exported?.success) {
              artifacts = {
                patternDraftsCsvPath: exported.patternDraftsCsvPath,
                defectMappingCsvPath: exported.defectMappingCsvPath,
                testcaseLibraryXlsxPath: exported.testcaseLibraryXlsxPath,
              };
            } else {
              artifactError = exported?.error || "failed to export reusable pattern review artifacts";
            }
          } catch (error) {
            artifactError = String(error?.message || error);
          }
        }
        sendJson(response, 200, {
          success: true,
          sourceSnapshot: prepared.sourceSnapshot,
          results,
          summary: { total: results.length, succeeded, failed: results.length - succeeded },
          artifacts,
          artifactError: artifactError || undefined,
        });
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    // Export reusable pattern drafts to CSV (pattern-drafts + defect-mapping).
    // Called after all batches of generate-pattern-drafts (with skip_export=true)
    // complete, so the full result set is written to a single pair of CSV files.
    if (request.method === "POST" && url.pathname === "/api/create-testcase/export-pattern-drafts") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const runId = typeof body?.run_id === "string" ? body.run_id.trim() : "";
      const drafts = Array.isArray(body?.drafts) ? body.drafts : [];

      if (!runId) {
        sendJson(response, 400, { success: false, error: "run_id is required" });
        return;
      }
      if (drafts.length === 0) {
        sendJson(response, 400, { success: false, error: "drafts[] is required and must be non-empty" });
        return;
      }

      try {
        const exported = await runTestCaseBridge(buildReusablePatternDraftExportPayload({ runId, drafts }));
        if (!exported?.success) {
          sendJson(response, 500, { success: false, error: exported?.error || "failed to export reusable pattern review artifacts" });
          return;
        }
        sendJson(response, 200, {
          success: true,
          artifacts: {
            patternDraftsCsvPath: exported.patternDraftsCsvPath,
            defectMappingCsvPath: exported.defectMappingCsvPath,
            testcaseLibraryXlsxPath: exported.testcaseLibraryXlsxPath,
          },
          patternCount: exported.patternCount,
          mappingRowCount: exported.mappingRowCount,
        });
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    // Export generated test-case drafts to a CSV file (no Octane write).
    // The drafts come from prepare-batch; this writes them to exports/testcases/.
    if (request.method === "POST" && url.pathname === "/api/create-testcase/export-csv") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const drafts = Array.isArray(body?.drafts) ? body.drafts : [];
      const keyword = typeof body?.keyword === "string" ? body.keyword.trim() : "testcases";
      const filename = typeof body?.filename === "string" ? body.filename.trim() : "";

      if (drafts.length === 0) {
        sendJson(response, 400, { success: false, error: "drafts[] is required and must be non-empty" });
        return;
      }

      try {
        const result = await runTestCaseBridge({
          action: "export_csv",
          keyword,
          filename,
          drafts,
          cluster: body?.cluster !== false,  // default: cluster on
          threshold: Number(body?.threshold) || 0.85,
        });
        sendJson(response, result?.success ? 200 : 500, result);
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase/prepare-batch") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const { batchSize, concurrency } = resolveTestcaseBatchConfig();
      const defectIds = Array.isArray(body?.defect_ids)
        ? body.defect_ids.map((id) => String(id || "").trim()).filter(Boolean)
        : [];
      const intent = typeof body?.intent === "string" ? body.intent.trim() : "";

      if (defectIds.length === 0) {
        sendJson(response, 400, { success: false, error: "defect_ids[] is required" });
        return;
      }
      if (defectIds.length > batchSize) {
        sendJson(response, 400, { success: false, error: `max ${batchSize} defects per batch` });
        return;
      }

      const chatConfig = resolveChatModelConfig(undefined, process.env);

      try {
        const results = await mapWithConcurrency(defectIds, concurrency, async (defectId) => {
          try {
            // Step 1: Python bridge — prepare (read defect + RAG retrieve similar cases)
            const prepared = await runTestCaseBridge({ action: "prepare", defect_id: defectId });
            if (!prepared?.success || !prepared?.defect_info) {
              return {
                defectId,
                defectName: "",
                defectSeverity: "",
                defectSoftwareVersion: "",
                defectAssignedEcu: "",
                defectLeadModel: "",
                name: "",
                descriptionHtml: "",
                stepsText: "",
                verification: { passed: false, criteria: [], feedback: "" },
                similarCases: [],
                generatedAt: new Date().toISOString(),
                error: prepared?.error || "failed to prepare defect",
              };
            }

            const defectInfo = prepared.defect_info;
            const similarCases = prepared.similar_cases || [];
            const fewShotText = prepared.few_shot_text || "";

            // Step 2: LLM generation — build description HTML + steps text
            const llmResult = await generateTestCaseContent(defectInfo, fewShotText, chatConfig, intent);

            // Step 3: Python bridge — verify quality
            const verified = await runTestCaseBridge({
              action: "verify",
              defect_info: defectInfo,
              description_html: llmResult.descriptionHtml,
              steps_text: llmResult.stepsText,
            });

            return {
              defectId: defectInfo.defect_id,
              defectName: defectInfo.name,
              defectSeverity: defectInfo.severity,
              defectSoftwareVersion: defectInfo.software_version,
              defectAssignedEcu: defectInfo.assigned_ecu,
              defectLeadModel: defectInfo.lead_model,
              name: llmResult.testName,
              descriptionHtml: llmResult.descriptionHtml,
              stepsText: llmResult.stepsText,
              verification: verified?.verification || { passed: false, criteria: [], feedback: "verification skipped" },
              similarCases,
              generatedAt: new Date().toISOString(),
              error: null,
            };
          } catch (err) {
            return {
              defectId,
              defectName: "",
              defectSeverity: "",
              defectSoftwareVersion: "",
              defectAssignedEcu: "",
              defectLeadModel: "",
              name: "",
              descriptionHtml: "",
              stepsText: "",
              verification: { passed: false, criteria: [], feedback: "" },
              similarCases: [],
              generatedAt: new Date().toISOString(),
              error: String(err?.message || err),
            };
          }
        });

        const succeeded = results.filter((r) => r && !r.error).length;
        sendJson(response, 200, {
          success: true,
          results,
          summary: { total: results.length, succeeded, failed: results.length - succeeded },
        });
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    // Generate a test case directly from a natural-language description (no
    // defect lookup). Used when the LLM intent analyzer decides the user is
    // describing a test scenario, not searching for existing defects.
    if (request.method === "POST" && url.pathname === "/api/create-testcase/generate-from-description") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const description = typeof body?.description === "string" ? body.description.trim() : "";

      if (!description) {
        sendJson(response, 400, { success: false, error: "description is required" });
        return;
      }

      try {
        const chatConfig = resolveChatModelConfig(undefined, process.env);

        // Step 1: RAG retrieve similar test cases for style reference.
        // Build a synthetic defect_info so generateTestCaseContent has the
        // fields it expects, and retrieve_similar_test_cases has a query string.
        const syntheticDefect = {
          defect_id: "",
          name: description,
          severity: "",
          software_version: "",
          assigned_ecu: "",
          lead_model: "",
          project: "",
          phase: "",
          description: description,
        };

        const prepared = await runTestCaseBridge({ action: "prepare", defect_id: description });
        // prepare may fail (no matching defect), but that's OK — we still
        // generate from the description. Use whatever similar cases we got.
        const fewShotText = prepared?.success ? (prepared.few_shot_text || "") : "";

        // Step 2: LLM generation
        const llmResult = await generateTestCaseContent(syntheticDefect, fewShotText, chatConfig, description);

        // Step 3: verify
        const verified = await runTestCaseBridge({
          action: "verify",
          defect_info: syntheticDefect,
          description_html: llmResult.descriptionHtml,
          steps_text: llmResult.stepsText,
        });

        const result = {
          defectId: "",
          defectName: description,
          defectSeverity: "",
          defectSoftwareVersion: "",
          defectAssignedEcu: "",
          defectLeadModel: "",
          name: llmResult.testName,
          descriptionHtml: llmResult.descriptionHtml,
          stepsText: llmResult.stepsText,
          verification: verified?.verification || { passed: false, criteria: [], feedback: "verification skipped" },
          similarCases: prepared?.success ? (prepared.similar_cases || []) : [],
          generatedAt: new Date().toISOString(),
          error: null,
        };

        sendJson(response, 200, {
          success: true,
          results: [result],
          summary: { total: 1, succeeded: 1, failed: 0 },
        });
      } catch (error) {
        sendJson(response, 500, { success: false, error: String(error?.message || error) });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/create-testcase/commit-batch") {
      if (!await requireInternalAuxiliaryActor(request, response)) return;
      const body = await readJsonBody(request);
      const items = Array.isArray(body?.items) ? body.items : [];
      const ownerId = typeof body?.owner_workspace_user_id === "string" ? body.owner_workspace_user_id.trim() : "";

      if (items.length === 0) {
        sendJson(response, 400, { success: false, error: "items[] is required" });
        return;
      }

      // Serial commit — write_test_steps is per-test-id and cannot be batched.
      // A single failure does not block subsequent items.
      const results = [];
      for (const item of items) {
        const testCaseData = item?.test_case_data;
        const defectId = typeof testCaseData?.defectId === "string" ? testCaseData.defectId : "";
        const featureId = typeof item?.feature_id === "string" ? item.feature_id.trim() : "";
        if (!testCaseData) {
          results.push({ defectId, success: false, error: "test_case_data is required" });
          continue;
        }
        try {
          const result = await runTestCaseBridge({
            action: "commit",
            test_case_data: { ...testCaseData, owner_workspace_user_id: ownerId },
            feature_id: featureId,
          });
          results.push({
            defectId,
            success: Boolean(result?.success),
            test_id: result?.test_id || "",
            octane_url: result?.octane_url || "",
            error: result?.success ? null : (result?.error || "commit failed"),
          });
        } catch (err) {
          results.push({ defectId, success: false, error: String(err?.message || err) });
        }
      }

      const succeeded = results.filter((r) => r.success).length;
      sendJson(response, 200, {
        success: true,
        results,
        summary: { total: results.length, succeeded, failed: results.length - succeeded },
      });
      return;
    }

    if (serveStaticAsset(request, response, url)) {
      return;
    }

    sendJson(response, 404, { success: false, error: `Not found: ${url.pathname}` });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown server error";
    sendJson(response, 500, { success: false, error: message });
  }
});

server.listen(port, localHttpBoundary.listenHost, () => {
  console.log(`Vizion local API listening on http://${localHttpBoundary.listenHost}:${port}`);
  duplicateWarmupManager.triggerBackgroundWarmup({ reason: "startup" });
});

function shutdown() {
  stopDuplicateBridgeRuntime();
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
