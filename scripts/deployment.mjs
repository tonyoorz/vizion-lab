import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { resolveChatModelConfig } from "../server/chatModelConfig.mjs";
import { resolvePythonCommand } from "./devHelpers.mjs";

export const REQUIRED_PYTHON_MODULES = [
  "numpy", "pandas", "sklearn", "sentence_transformers", "fastapi", "uvicorn",
  "httpx", "duckdb", "requests", "urllib3", "tqdm", "playwright", "rapidocr",
  "onnxruntime", "pypdf", "openpyxl", "session_keeper",
];
const ASR_MODULES = ["funasr", "modelscope", "torch", "torchaudio"];

export const PYTHON_PROBE = `
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys

names = json.loads(sys.argv[1])
modules = {}
ocr_models = False
for name in names:
    try:
        spec = importlib.util.find_spec(name)
        modules[name] = spec is not None
        if name == "rapidocr" and spec and spec.origin:
            ocr_models = any(Path(spec.origin).parent.glob("models/*.onnx"))
    except (ImportError, ValueError, AttributeError):
        modules[name] = False
chromium = False
if modules.get("playwright"):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as browser:
            chromium = Path(browser.chromium.executable_path).is_file()
    except Exception:
        pass
versions = {}
for name in ["bmw-tools", "funasr", "torch", "torchaudio", "modelscope", "rapidocr", "playwright"]:
    try:
        versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        pass
print(json.dumps({"version": platform.python_version(), "platform": sys.platform,
                  "machine": platform.machine(), "modules": modules,
                  "chromium": chromium, "ocrModels": ocr_models, "versions": versions}))
`;

export function nativePythonPath(repoRoot, platform = process.platform) {
  return path.join(repoRoot, ".venv", platform === "win32" ? "Scripts" : "bin", platform === "win32" ? "python.exe" : "python");
}

export function createSetupPlan({
  repoRoot,
  platform = process.platform,
  basePython = platform === "win32" ? "python" : "python3",
  npmCli = process.env.npm_execpath,
  keeperSource,
  asr = false,
  pipHelper,
  homeDir = os.homedir(),
  env = process.env,
}) {
  if (!npmCli) {
    throw new Error("Run setup through npm run deploy:setup so the npm CLI path is available.");
  }
  const python = nativePythonPath(repoRoot, platform);
  if (fs.existsSync(path.join(repoRoot, ".venv")) && !fs.existsSync(python)) {
    throw new Error("The .venv directory is foreign or incomplete. Rename it and rerun setup; never copy virtual environments between machines.");
  }
  const helper = pipHelper || (platform === "win32" ? path.join(homeDir, ".copilot", "corp-pip-proxy.ps1") : "");
  if (pipHelper && !fs.existsSync(pipHelper)) {
    throw new Error("The specified corporate pip helper does not exist.");
  }
  const pipStep = (id, args) => helper && fs.existsSync(helper)
    ? { id, command: "powershell.exe", args: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper, python, "-m", "pip", "install", ...args] }
    : { id, command: python, args: ["-m", "pip", "install", ...args] };
  const steps = [{ id: "javascript-dependencies", command: process.execPath, args: [npmCli, "ci"] }];
  if (!fs.existsSync(python)) {
    steps.push({ id: "venv", command: basePython, args: ["-m", "venv", path.join(repoRoot, ".venv")] });
  }
  steps.push(pipStep("python-dependencies", ["-r", path.join(repoRoot, "requirements.txt")]));
  if (keeperSource) {
    if (!fs.existsSync(keeperSource)) {
      throw new Error("Keeper source must be an existing trusted local BmwLogin checkout or wheel.");
    }
    steps.push(pipStep("session-keeper", [`${path.resolve(keeperSource)}[all,session-keeper]`]));
  }
  if (asr) {
    steps.push(pipStep("asr", ["-r", path.join(repoRoot, "requirements-asr.txt")]));
  }
  steps.push({ id: "chromium", command: python, args: ["-m", "playwright", "install", "chromium"] });
  return steps.map((step) => ({ ...step, cwd: repoRoot, env }));
}

export function initializeLocalEnvironment(repoRoot) {
  const destination = path.join(repoRoot, ".env.local");
  if (fs.existsSync(destination)) {
    return false;
  }
  fs.copyFileSync(path.join(repoRoot, ".env.example"), destination, fs.constants.COPYFILE_EXCL);
  fs.chmodSync(destination, 0o600);
  return true;
}

export function safeEndpoint(value) {
  if (!value) {
    return null;
  }
  try {
    const url = new URL(value);
    return `${url.protocol}//${url.host}${url.pathname}`;
  } catch {
    return "[configured endpoint]";
  }
}

export function probePython({ repoRoot, env = process.env, python } = {}) {
  const command = python || resolvePythonCommand({ repoRoot, env });
  const result = spawnSync(command, ["-c", PYTHON_PROBE, JSON.stringify([...REQUIRED_PYTHON_MODULES, ...ASR_MODULES])], {
    cwd: repoRoot,
    env: { ...env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    encoding: "utf8",
    timeout: 30000,
    windowsHide: true,
  });
  if (result.status !== 0) {
    return { version: null, modules: {}, chromium: false, error: "Python probe failed; verify the native interpreter and its dependencies." };
  }
  try {
    return JSON.parse(result.stdout.trim());
  } catch {
    return { version: null, modules: {}, chromium: false, error: "Python probe did not return valid JSON." };
  }
}

function hasWeights(directory) {
  if (!fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    return false;
  }
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) continue;
    if (entry.isFile() && /\.(onnx|safetensors|bin|pt|pth)$/i.test(entry.name)) return true;
    if (entry.isDirectory() && hasWeights(path.join(directory, entry.name))) return true;
  }
  return false;
}

async function fetchJson(url, fetchImpl) {
  try {
    const response = await fetchImpl(url, { signal: AbortSignal.timeout(5000) });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function inspectDeployment({
  repoRoot,
  homeDir = os.homedir(),
  env = process.env,
  probe = probePython({ repoRoot, env }),
  commandAvailable = (command) => spawnSync(command, ["-version"], { stdio: "ignore", windowsHide: true }).status === 0,
  services = false,
  expectedTools = ["octane"],
  fetchImpl = globalThis.fetch,
} = {}) {
  const checks = [];
  const add = (id, present, message, missingStatus = "fail") => checks.push({ id, status: present ? "pass" : missingStatus, message });
  const sourceDb = env.VIZION_FULL_PICTURE_SOURCE_DB_PATH || path.join(env.VIZION_DATABASE_ROOT || path.join(repoRoot, "database"), "source", "qgate_raw.db");
  const hotDb = env.VIZION_FULL_PICTURE_HOT_DB_PATH || path.join(env.VIZION_DATABASE_ROOT || path.join(repoRoot, "database"), "hot", "vizion_serving.db");
  add("node", Number(process.versions.node.split(".")[0]) === 24, "Use Node 24.x.");
  add("python", /^3\.(11|12|13)\./.test(probe.version || ""), "Use native Python 3.11-3.13; the source machine uses 3.13.9.");
  add("javascript.dependencies", fs.existsSync(path.join(repoRoot, "node_modules", "vite", "package.json")), "Install JavaScript dependencies with the tracked lockfile.");
  for (const name of REQUIRED_PYTHON_MODULES) {
    add(`python.module.${name}`, probe.modules?.[name] === true, name === "session_keeper"
      ? "Install trusted BmwLogin with [all,session-keeper] extras."
      : `Required Python module: ${name}.`);
  }
  add("browser.chromium", probe.chromium === true, "Install the Python Playwright Chromium browser.");
  const hasDatabase = (filePath) => fs.existsSync(filePath) && fs.statSync(filePath).isFile() && fs.statSync(filePath).size > 0;
  add("database.source", hasDatabase(sourceDb), "Restore the private SQLite source snapshot or synchronize authorized Octane data.");
  add("database.hot", hasDatabase(hotDb), "Restore the hot database or run refresh-full-picture-outcomes.");
  add("model.embedding", hasWeights(path.join(repoRoot, "backend", "cache", "embedding_models")), "Restore the local sentence-transformer model cache for full duplicate search.");
  const config = resolveChatModelConfig("", env);
  add("chat.credential", Boolean(config.credential), "Configure model access privately in .env.local or the process environment.");
  for (const key of ["UPSTREAM_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "VIZION_ANALYTICS_PYTHON", "DUPSEARCH_AGENT_PYTHON", "DUPSEARCH_LOCAL_ASR_PYTHON", "DUPSEARCH_LOCAL_ASR_FFMPEG"]) {
    if (env[key] && !["python", "python3", "ffmpeg"].includes(env[key])) {
      add(`config.path.${key}`, fs.existsSync(env[key]), `${key} must point to a file on this computer, not the previous machine.`);
    }
  }
  const provider = env.DUPSEARCH_TRANSCRIBE_PROVIDER || "local-funasr";
  const localAsr = provider !== "beacon-doubao" || env.DUPSEARCH_BEACON_DOUBAO_ASR_FALLBACK_TO_LOCAL !== "false";
  if (localAsr) {
    for (const name of ASR_MODULES) add(`python.module.${name}`, probe.modules?.[name] === true, `Local transcription requires ${name}; run setup --asr.`);
    add("asr.ffmpeg", commandAvailable(env.DUPSEARCH_LOCAL_ASR_FFMPEG || "ffmpeg"), "Install ffmpeg and make it available on PATH.");
    const model = env.DUPSEARCH_LOCAL_ASR_MODEL;
    const modelPath = model && model !== "iic/SenseVoiceSmall" ? model : path.join(homeDir, ".cache", "modelscope", "iic", "SenseVoiceSmall");
    add("model.asr", hasWeights(modelPath), "Restore SenseVoiceSmall or configure its native local path.");
  }
  if (provider === "beacon-doubao") {
    add("asr.beacon", Boolean(env.DUPSEARCH_BEACON_DOUBAO_ASR_URL && env.DUPSEARCH_BEACON_DOUBAO_ASR_API_KEY), "Beacon ASR needs a private adapter URL and credential.");
  }
  add("model.ocr", probe.ocrModels === true || hasWeights(path.join(homeDir, ".cache", "modelscope", "RapidAI", "RapidOCR")), "OCR needs the bundled RapidOCR weights or the restored local OCR cache.");
  if (services) {
    const keeperUrl = String(env.SESSION_KEEPER_URL || "http://127.0.0.1:18090").replace(/\/$/, "");
    const keeperHealth = await fetchJson(`${keeperUrl}/health`, fetchImpl);
    add("service.keeper", keeperHealth?.status === "ok" && Number.isInteger(keeperHealth.active_sessions), "Session Keeper must be running and healthy.");
    const tools = await fetchJson(`${keeperUrl}/tools`, fetchImpl);
    const toolNames = tools && typeof tools === "object" ? Object.keys(tools).sort() : [];
    for (const tool of expectedTools) add(`keeper.tool.${tool}`, toolNames.includes(tool), `Required keeper adapter: ${tool}; authorization must still be checked interactively.`);
    for (const [name, port, expectedService] of [["analytics", env.VIZION_ANALYTICS_PORT || 3003, "analytics"], ["local-api", env.VIZION_API_PORT || 3004, null]]) {
      const health = await fetchJson(`http://127.0.0.1:${port}/health`, fetchImpl);
      add(`service.${name}`, health?.ok === true && (!expectedService || health.service === expectedService), `${name} must return its expected health response.`);
    }
    try {
      const response = await fetchImpl(`http://127.0.0.1:${env.VIZION_WEB_PORT || 8080}/`, { signal: AbortSignal.timeout(5000) });
      add("service.frontend", response.ok, "Frontend must respond on the configured web port.");
    } catch {
      add("service.frontend", false, "Frontend must respond on the configured web port.");
    }
    if (config.baseUrl) {
      try {
        const response = await fetchImpl(`${config.baseUrl.replace(/\/+$/, "")}/models`, {
          signal: AbortSignal.timeout(5000),
          headers: { Authorization: `Bearer ${config.credential}` },
        });
        add("service.models", response.ok, "Configured OpenAI-compatible service must answer /models; this does not verify LLM tool calls.");
      } catch {
        add("service.models", false, "Start/configure the separate GLMProxy service; it is not launched by npm run dev.");
      }
    } else {
      checks.push({ id: "service.models", status: "warn", message: "Company model endpoints need an actual authorized chat/tool-call acceptance check." });
    }
    return finishReport(checks, env, probe, config, toolNames);
  }
  checks.push({ id: "services", status: "warn", message: "Runtime services were not checked; rerun doctor --services after npm run dev." });
  return finishReport(checks, env, probe, config, []);
}

function finishReport(checks, env, probe, config, keeperTools) {
  return {
    schemaVersion: 1,
    platform: process.platform,
    architecture: process.arch,
    node: process.versions.node,
    python: probe.version || null,
    versions: probe.versions || {},
    ready: !checks.some((check) => check.status === "fail"),
    featureAcceptanceRequired: true,
    configuredKeys: Object.keys(env).filter((key) => /^(VIZION_|DUPSEARCH_|GLM_PROXY_|SESSION_|UPSTREAM_|NODE_EXTRA_CA_CERTS$)/.test(key)).sort(),
    modelEndpoint: safeEndpoint(config.endpoint || config.baseUrl),
    keeperTools,
    checks,
  };
}