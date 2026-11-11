import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { loadLocalEnv } from "../server/loadLocalEnv.mjs";
import { resolvePythonInvocation } from "./devHelpers.mjs";

loadLocalEnv();
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { command, args, ...options } = resolvePythonInvocation({ repoRoot, args: process.argv.slice(2) });
const result = spawnSync(command, args, { ...options, stdio: "inherit", windowsHide: true });
if (result.error) console.error("Could not start native Python. Run npm run deploy:doctor to check the interpreter.");
process.exitCode = result.status ?? 1;