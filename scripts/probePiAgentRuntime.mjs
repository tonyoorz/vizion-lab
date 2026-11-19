import fs from "node:fs";
import { isDeepStrictEqual } from "node:util";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";

import { runMainAgentToolTurn, runPiMainAgentToolTurn } from "../server/mainAgentToolOrchestrator.mjs";
import { executeMainAgentToolCall } from "../server/mainAgentTools.mjs";
import { evaluateSemanticEvidence } from "../server/mainAgentEvidence.mjs";
import { verifyActorCapabilityHeader } from "../server/agentActorCapability.mjs";
import { validateAnswerTextCitations } from "../server/answerValidator.mjs";
import { createOntologyRegistry } from "../server/ontology/registry.mjs";

const EVAL_SECRET = "pi-synthetic-probe-capability-secret";
const EVAL_NOW = 1_700_000_000;
const ACTOR = {
  actorId: "pi-synthetic-reader", scopeHash: "scope-eval-dtsv",
  scopes: { allowedObjectTypes: ["quality.defect"], teamIds: ["DTSV_China"], projectIds: ["IDCEVO"] },
};

function defaultCases() {
  return fs.readFileSync(new URL("../evals/main-agent/target/execution-golden.jsonl", import.meta.url), "utf8")
    .trim().split("\n").map((line) => JSON.parse(line));
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function parsedJson(value) {
  try { return JSON.parse(value); } catch { return value; }
}

export function fingerprintToolTurnContract(result, requests = []) {
  const contract = {
    toolCalls: (result.toolCalls || []).map((call) => ({
      id: call.id, name: call.function.name, arguments: parsedJson(call.function.arguments),
    })),
    toolMessages: (result.toolMessages || []).map((message) => ({ ...message, content: parsedJson(message.content) })),
    evidence: result.evidence || [], contextText: result.contextText || "", toolEvents: result.toolEvents || [], requests,
  };
  return createHash("sha256").update(canonicalJson(contract)).digest("hex");
}

async function runCase(item, runTurn, registry) {
  const plans = structuredClone(item.plans);
  const responses = structuredClone(item.fakeResponses);
  const endpoints = [];
  const requests = [];
  let modelCalls = 0;
  const startedAt = performance.now();
  const result = await runTurn({
    messages: [{ role: "user", content: item.query }],
    model: "synthetic-company-completion",
    requestToolCompletion: async () => {
      modelCalls += 1;
      return {
        content: "", toolCalls: (plans.shift() || []).map((call) => ({
          id: call.id, type: "function", function: { name: call.name, arguments: JSON.stringify(call.arguments) },
        })),
      };
    },
    executeToolCall: executeMainAgentToolCall,
    toolDependencies: {
      analyticsApiBase: "http://pi-synthetic-probe.local",
      analyticsFetch: async (url, init) => {
        const endpoint = new URL(url).pathname;
        endpoints.push(endpoint);
        requests.push({ endpoint, body: parsedJson(init.body) });
        if (endpoint.startsWith("/api/agent/")) {
          const actor = verifyActorCapabilityHeader(init.headers, { secret: EVAL_SECRET, now: EVAL_NOW });
          if (actor.scopeHash !== item.expected.actorScopeHash) throw new Error("Synthetic actor scope mismatch");
        }
        if (endpoint.startsWith("/api/semantic/")
          && JSON.parse(init.body).actorScope?.scopeHash !== item.expected.actorScopeHash) {
          throw new Error("Synthetic semantic scope mismatch");
        }
        const response = responses.shift();
        if (!response) throw new Error("Unexpected synthetic API request");
        return { ok: response.status >= 200 && response.status < 300, status: response.status, json: async () => response.body };
      },
      actor: ACTOR, actorCapabilitySecret: EVAL_SECRET, actorCapabilityNow: EVAL_NOW,
      actorCapabilityNonce: `pi-synthetic-${item.caseId}`, toolRecoveryWait: async () => {}, toolRecoveryRandom: () => 0,
    },
    maxSteps: 4, now: new Date("2026-08-04T08:00:00Z"), runId: `pi-probe-${item.caseId}`, threadId: "pi-synthetic-probe",
  });
  const toolSequence = result.toolCalls.map((call) => call.function.name);
  const evidenceStatus = evaluateSemanticEvidence(result.evidence).status;
  const citationValid = !item.expected.citation || validateAnswerTextCitations({
    text: item.expected.citation, evidence: result.evidence, registry,
  }).valid;
  return {
    toolSequence, endpoints, evidenceStatus, evidenceCount: result.evidence.length,
    stoppedReason: result.stoppedReason, modelCalls, durationMs: Number((performance.now() - startedAt).toFixed(2)),
    tokenUsage: null,
    contractFingerprint: fingerprintToolTurnContract(result, requests),
    passed: isDeepStrictEqual(toolSequence, item.expected.toolSequence)
      && isDeepStrictEqual(endpoints, item.expected.endpointSequence)
      && evidenceStatus === item.expected.evidenceStatus && citationValid && responses.length === 0,
    piToolExecutions: result.piEvents?.filter((event) => event.type === "tool_execution_start").length ?? null,
  };
}

export async function runPiRuntimeProbe({ cases = defaultCases() } = {}) {
  const registry = createOntologyRegistry();
  const previousFetch = globalThis.fetch;
  let networkRequests = 0;
  globalThis.fetch = async () => {
    networkRequests += 1;
    throw new Error("Network access is disabled in the synthetic Pi probe");
  };
  const comparisons = [];
  try {
    for (const item of cases) {
      const existing = await runCase(item, runMainAgentToolTurn, registry);
      const pi = await runCase(item, runPiMainAgentToolTurn, registry);
      const parity = existing.passed && pi.passed
        && isDeepStrictEqual(existing.toolSequence, pi.toolSequence)
        && isDeepStrictEqual(existing.endpoints, pi.endpoints)
        && existing.evidenceCount === pi.evidenceCount
        && existing.contractFingerprint === pi.contractFingerprint
        && existing.stoppedReason === pi.stoppedReason
        && existing.modelCalls === pi.modelCalls;
      comparisons.push({ caseId: item.caseId, parity, existing, pi });
    }
  } finally {
    globalThis.fetch = previousFetch;
  }
  return {
    dataSource: "synthetic", baseline: "existing-governed-tool-turn", candidate: "pi-agent-core-1.5.2",
    parity: comparisons.length > 0 && comparisons.every((item) => item.parity) && networkRequests === 0,
    networkRequests, cases: comparisons,
    limitations: [
      "No real model, database, upstream synchronization, or production runtime switch was used.",
      "Durations measure synthetic adapter execution, not model latency or token cost.",
      "This compares tool-turn contracts, not the full LangGraph Chat or frontend session pipeline.",
    ],
  };
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  if (process.argv.length !== 2) {
    console.error("The Pi probe accepts no arguments and uses synthetic fixtures only.");
    process.exitCode = 1;
  } else {
    try {
      const report = await runPiRuntimeProbe();
      console.log(JSON.stringify(report, null, 2));
      if (!report.parity) process.exitCode = 1;
    } catch {
      console.error("Synthetic Pi comparison failed before producing a complete report.");
      process.exitCode = 1;
    }
  }
}