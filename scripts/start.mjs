import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadLocalEnv } from "../server/loadLocalEnv.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..");

// Load .env.local into process.env before spawning the server child process.
// NODE_EXTRA_CA_CERTS (propagated from UPSTREAM_CA_BUNDLE in loadLocalEnv) is
// read by Node only at process startup, so it must be in the child's real
// environment — which requires a parent-process loader like this. Mirrors the
// `npm run dev` (scripts/dev.mjs) pattern, minus the dev stack (vite/analytics).
loadLocalEnv();

const child = spawn(process.execPath, [path.join(repoRoot, "server", "index.mjs")], {
  cwd: repoRoot,
  env: process.env,
  stdio: "inherit",
});

child.on("exit", (code, signal) => {
  const reason = signal ? `signal ${signal}` : `code ${code ?? 0}`;
  if (code !== 0 && code !== null && code !== undefined) {
    console.error(`[vizion-start] server exited with ${reason}`);
    process.exit(code);
  }
  process.exit(code ?? 0);
});

const shutdown = (signal) => {
  child.kill(signal || "SIGTERM");
};
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
