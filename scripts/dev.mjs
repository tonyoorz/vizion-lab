import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadLocalEnv } from "../server/loadLocalEnv.mjs";

import {
  getTerminationCommand,
  hasHealthyServiceOnPort,
  hasTcpServiceOnPort,
  hasViteDevServerOnPort,
  resolveLocalApiEnvironment,
  resolvePythonCommand,
  waitForHealthyService,
} from "./devHelpers.mjs";
import { assertSupportedNodeVersion } from "./nodeVersion.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..");
loadLocalEnv();
Object.assign(process.env, resolveLocalApiEnvironment(process.env));
process.env.SESSION_KEEPER_URL ||= "http://127.0.0.1:18090";
process.env.SESSION_STORE_DIR ||= path.join(repoRoot, "backend", "cache", "session-keeper");
const analyticsPort = Number(process.env.VIZION_ANALYTICS_PORT || "3003");
const apiPort = Number(process.env.VIZION_API_PORT || "3004");
const webPort = Number(process.env.VIZION_WEB_PORT || "8080");
const webHost = String(process.env.VIZION_WEB_HOST || process.env.HOST || "0.0.0.0").trim() || "0.0.0.0";
const sessionKeeperUrl = new URL(process.env.SESSION_KEEPER_URL);
const useLocalSessionKeeper = sessionKeeperUrl.protocol === "http:" &&
  ["localhost", "127.0.0.1"].includes(sessionKeeperUrl.hostname);

assertSupportedNodeVersion();

const children = [];
let exiting = false;
const serviceWaitOptions = { attempts: 30, delayMs: 500 };

function terminateChild(child, signal = "SIGTERM") {
  if (!child || child.killed) {
    return;
  }

  const termination = getTerminationCommand(process.platform, child.pid, signal);
  if (!termination) {
    return;
  }

  if (termination.command) {
    spawnSync(termination.command, termination.args, { stdio: "ignore" });
    return;
  }

  child.kill(termination.args[0]);
}

function launch(command, args, name, isHealthy) {
  const child = spawn(command, args, {
    cwd: repoRoot,
    env: process.env,
    stdio: "inherit",
  });

  child.on("exit", async (code, signal) => {
    if (exiting) {
      return;
    }

    if (await waitForHealthyService(isHealthy, serviceWaitOptions)) {
      console.warn(`[vizion-dev] ${name} process exited but service is still healthy; keeping dev stack running`);
      return;
    }

    const reason = signal ? `signal ${signal}` : `code ${code ?? 0}`;
    console.error(`[vizion-dev] ${name} exited with ${reason}`);
    exiting = true;
    for (const proc of children) {
      if (proc !== child && !proc.killed) {
        terminateChild(proc, signal || "SIGTERM");
      }
    }
    process.exit(code ?? 0);
  });

  children.push(child);
  console.log(`[vizion-dev] started ${name}`);
}

async function launchIfNeeded({ name, command, args, port, expectedService, isHealthy: checkHealth, waitUntilHealthy = false }) {
  if (exiting) {
    return;
  }

  const isHealthy = checkHealth || (() => hasHealthyServiceOnPort({ port, expectedService }));
  const healthy = await isHealthy();
  if (exiting) {
    return;
  }

  if (healthy) {
    console.log(`[vizion-dev] reusing existing ${name} on http://127.0.0.1:${port}`);
    return;
  }

  const portInUse = await hasTcpServiceOnPort({ port });
  if (exiting) {
    return;
  }

  if (portInUse) {
    console.log(`[vizion-dev] waiting for existing ${name} on http://127.0.0.1:${port} to become healthy`);
    const existingServiceHealthy = await waitForHealthyService(isHealthy, serviceWaitOptions);
    if (exiting) {
      return;
    }
    if (existingServiceHealthy) {
      console.log(`[vizion-dev] reusing existing ${name} on http://127.0.0.1:${port}`);
      return;
    }

    console.error(`[vizion-dev] port ${port} is already in use, but ${name} did not report a healthy /health response`);
    exiting = true;
    for (const child of children) {
      terminateChild(child);
    }
    process.exit(1);
  }

  launch(command, args, name, isHealthy);
  if (waitUntilHealthy) {
    const ready = await waitForHealthyService(isHealthy, serviceWaitOptions);
    if (exiting) {
      return;
    }
    if (!ready) {
      console.error(`[vizion-dev] ${name} did not become healthy on http://127.0.0.1:${port}`);
      shutdown("SIGTERM");
      process.exit(1);
    }
    console.log(`[vizion-dev] ${name} ready on http://127.0.0.1:${port}`);
  }
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("exit", () => shutdown("SIGTERM"));

if (useLocalSessionKeeper) {
  const sessionKeeperPort = Number(sessionKeeperUrl.port || "80");
  await launchIfNeeded({
    name: "session-keeper",
    command: resolvePythonCommand({ repoRoot }),
    args: [
      "-m",
      "uvicorn",
      "session_keeper.app:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(sessionKeeperPort),
    ],
    port: sessionKeeperPort,
    expectedService: "session-keeper",
    waitUntilHealthy: true,
  });
} else {
  console.log("[vizion-dev] using configured external session-keeper; skipping local launch");
}

await launchIfNeeded({
  name: "analytics-api",
  command: resolvePythonCommand({ repoRoot }),
  args: [
    "-m",
    "uvicorn",
    "backend.analytics.api:app",
    "--host",
    "127.0.0.1",
    "--port",
    String(analyticsPort),
  ],
  port: analyticsPort,
  expectedService: "analytics",
});
await launchIfNeeded({
  name: "local-api",
  command: process.execPath,
  args: [path.join(repoRoot, "server", "devLocalApi.mjs")],
  port: apiPort,
});
await launchIfNeeded({
  name: "vite",
  command: process.execPath,
  args: [path.join(repoRoot, "node_modules", "vite", "bin", "vite.js"), "--host", webHost, "--port", String(webPort)],
  port: webPort,
  isHealthy: () => hasViteDevServerOnPort({ port: webPort }),
});

function shutdown(signal) {
  if (exiting) {
    return;
  }

  exiting = true;
  for (const child of children) {
    terminateChild(child, signal);
  }
}
