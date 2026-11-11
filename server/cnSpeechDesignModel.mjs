import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import { loadLocalEnv } from "./loadLocalEnv.mjs";
import { buildChatCompletionRequest } from "./chatModelConfig.mjs";

loadLocalEnv();

export function buildDesignRequest(job, env = process.env) {
  const messages = job.operation === "probe"
    ? [{ role: "user", content: 'Return only this JSON object: {"status":"ok"}' }]
    : job.messages;
  if (!Array.isArray(messages) || messages.length === 0) {
    throw new Error("Missing messages");
  }
  const request = buildChatCompletionRequest({
    selectedModel: job.model || "deepseek-v4-flash",
    messages,
    env,
    maxTokens: job.operation === "probe" ? 512 : 8192,
    responseFormat: { type: "json_object" },
  });
  if (!request.config.credential) throw new Error("Configured model credential unavailable");
  if (["glm-5.3-flash", "deepseek-v4-flash", "deepseek-v4-pro"].includes(request.config.model.toLowerCase())) {
    request.body.thinking = { type: "disabled" };
  }
  return request;
}

export async function completeDesign(job, { env = process.env, fetchImpl = fetch } = {}) {
  const started = performance.now();
  const request = buildDesignRequest(job, env);
  const response = await fetchImpl(request.url, {
    method: "POST",
    headers: request.headers,
    body: JSON.stringify(request.body),
    signal: AbortSignal.timeout(180000),
  });
  if (!response.ok) {
    const error = new Error(`Configured model HTTP ${response.status}`);
    error.status = response.status;
    const retry = response.headers.get("retry-after");
    const seconds = Number(retry);
    error.retryAfterSeconds = Number.isFinite(seconds) && seconds > 0
      ? seconds
      : Math.max(0, (Date.parse(retry || "") - Date.now()) / 1000) || 0;
    throw error;
  }
  const payload = await response.json();
  if (payload.model && String(payload.model).toLowerCase() !== request.config.model.toLowerCase()) {
    throw new Error("The upstream response model does not match the requested model");
  }
  const content = payload?.choices?.[0]?.message?.content || "";
  if (!content.trim()) throw new Error("Configured model returned no design content");
  if (payload?.choices?.[0]?.finish_reason === "length") throw new Error("Configured model design was truncated");
  const cleaned = content.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  const design = JSON.parse(cleaned);
  return {
    ok: true,
    design,
    model: request.config.model,
    response_model: payload.model || null,
    elapsed_seconds: Math.round(performance.now() - started) / 1000,
    usage: payload.usage || {},
    route: "repository_configured_chatModelConfig",
  };
}

function failurePayload(error) {
  return {
    ok: false,
    error: error.message,
    status: error.status || null,
    retry_after_seconds: error.retryAfterSeconds || 0,
  };
}

async function serve() {
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const pending = new Set();
  for await (const line of lines) {
    if (!line.trim()) continue;
    if (pending.size >= 64) await Promise.race(pending);
    const task = (async () => {
      let job;
      try {
        job = JSON.parse(line);
        const result = await completeDesign(job);
        process.stdout.write(JSON.stringify({ request_id: job.request_id, ...result }) + "\n");
      } catch (error) {
        process.stdout.write(JSON.stringify({ request_id: job?.request_id, ...failurePayload(error) }) + "\n");
      }
    })();
    pending.add(task);
    task.finally(() => pending.delete(task));
  }
  await Promise.all(pending);
}

async function main() {
  if (process.argv.includes("--serve")) {
    await serve();
    return;
  }
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  process.stdout.write(JSON.stringify(await completeDesign(JSON.parse(input))));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stdout.write(JSON.stringify(failurePayload(error)));
    process.exitCode = 1;
  });
}