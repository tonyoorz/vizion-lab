import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { loadLocalEnv } from "../server/loadLocalEnv.mjs";
import { assertSupportedNodeVersion } from "./nodeVersion.mjs";
import { hasTcpServiceOnPort } from "./devHelpers.mjs";
import { createSetupPlan, initializeLocalEnvironment, inspectDeployment, nativePythonPath, probePython } from "./deployment.mjs";
import { exportTransfer, importTransfer, planTransfer } from "./deploymentTransfer.mjs";

const defaultRepoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const COMMAND_OPTIONS = {
  setup: ["python", "keeper-source", "asr", "pip-helper", "dry-run"],
  doctor: ["services", "json"],
  export: ["output", "with-data", "with-models", "with-reports", "dry-run"],
  import: ["input", "replace", "dry-run"],
};
const HELP = `Portable Vizion deployment (Node 24; Windows/macOS)

npm run deploy:setup -- --keeper-source ../BmwLogin --asr
npm run deploy:doctor -- --services
npm run deploy:export -- --output .deployment/transfer --with-data --with-models --with-reports --dry-run
npm run deploy:import -- --input /private/path/transfer --dry-run

Setup accepts --python <native executable> and --pip-helper <local PowerShell script>.
Export is private and unencrypted. Secrets, SSO sessions, code and virtual environments are excluded.
Import refuses existing files unless --replace is explicit; replaced files are backed up.
See docs/portable-deployment.md for external services, credentials and acceptance checks.`;

export function parseDeploymentArguments(argv) {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    strict: true,
    options: Object.fromEntries([
      ...["python", "keeper-source", "pip-helper", "output", "input"].map((name) => [name, { type: "string" }]),
      ...["asr", "services", "json", "with-data", "with-models", "with-reports", "replace", "dry-run", "help"].map((name) => [name, { type: "boolean" }]),
    ]),
  });
  const command = positionals[0] || "help";
  if (values.help || command === "help") return { command: "help", options: {} };
  if (!COMMAND_OPTIONS[command] || positionals.length !== 1) throw new Error("Unknown deployment command; use --help.");
  if (Object.keys(values).some((name) => !COMMAND_OPTIONS[command].includes(name))) throw new Error("This option is not supported for the selected command.");
  if (command === "export" && !values.output) throw new Error("Export requires --output <new private directory>.");
  if (command === "import" && !values.input) throw new Error("Import requires --input <trusted transfer directory>.");
  return { command, options: values };
}

function codeMetadata(repoRoot) {
  try {
    return {
      codeRevision: execFileSync("git", ["rev-parse", "HEAD"], { cwd: repoRoot, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim(),
      dirtyWorktree: Boolean(execFileSync("git", ["status", "--porcelain", "--untracked-files=no"], { cwd: repoRoot, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim()),
    };
  } catch {
    return { codeRevision: null, dirtyWorktree: true };
  }
}

function expectedKeeperTools(repoRoot) {
  const filePath = path.join(repoRoot, ".deployment", "source-inventory.json");
  if (!fs.existsSync(filePath)) return ["octane"];
  const inventory = JSON.parse(fs.readFileSync(filePath, "utf8"));
  const tools = (Array.isArray(inventory.keeperTools) ? inventory.keeperTools : []).filter((name) => typeof name === "string" && /^[a-z0-9_-]{1,80}$/i.test(name));
  return [...new Set(["octane", ...tools])];
}

export async function runDeployment(argv, { repoRoot = defaultRepoRoot, homeDir = os.homedir(), env = process.env, log = console.log } = {}) {
  const { command, options } = parseDeploymentArguments(argv);
  if (command === "help") {
    log(HELP);
    return { ready: true };
  }
  assertSupportedNodeVersion();
  if (command === "setup") {
    const steps = createSetupPlan({ repoRoot, homeDir, env, npmCli: env.npm_execpath, basePython: options.python, keeperSource: options["keeper-source"], asr: options.asr, pipHelper: options["pip-helper"] });
    if (options["dry-run"]) {
      const result = { dryRun: true, steps: steps.map(({ id, command: executable, args }) => ({ id, command: executable, args })) };
      log(JSON.stringify(result, null, 2));
      return result;
    }
    const python = fs.existsSync(nativePythonPath(repoRoot)) ? nativePythonPath(repoRoot) : options.python || (process.platform === "win32" ? "python" : "python3");
    const probe = probePython({ repoRoot, env, python });
    if (!/^3\.(11|12|13)\./.test(probe.version || "")) throw new Error("Native Python 3.11-3.13 is required. Prefer 3.13 and specify --python <executable> if the default differs.");
    if (!options["keeper-source"] && !probe.modules?.session_keeper) throw new Error("Clone the trusted BmwLogin repository first and pass --keeper-source ../BmwLogin; see the deployment guide.");
    for (const step of steps) {
      log(`[setup] ${step.id}`);
      const result = spawnSync(step.command, step.args, { cwd: repoRoot, env: { ...env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" }, stdio: "inherit", windowsHide: true });
      if (result.status !== 0) throw new Error(`Installation step failed: ${step.id}. No credentials were changed. Resolve the terminal error and rerun setup.`);
    }
    initializeLocalEnvironment(repoRoot);
    log("Installation steps completed. Configure private credentials, restore models/data, start external services, then run deploy:doctor -- --services.");
    return { installed: true, featureAcceptanceRequired: true };
  }
  if (command === "doctor") {
    const report = await inspectDeployment({ repoRoot, homeDir, env, services: options.services, expectedTools: expectedKeeperTools(repoRoot) });
    Object.assign(report, codeMetadata(repoRoot));
    if (options.json) log(JSON.stringify(report, null, 2));
    else {
      for (const check of report.checks) log(`${check.status.toUpperCase().padEnd(4)} ${check.id}: ${check.message}`);
      log(`\nPrerequisites: ${report.ready ? "PASS" : "INCOMPLETE"}. Full feature acceptance is still required.`);
    }
    return report;
  }
  if (command === "export") {
    const transferOptions = { repoRoot, homeDir, env, includeData: options["with-data"], includeModels: options["with-models"], includeReports: options["with-reports"] };
    const entries = planTransfer(transferOptions);
    if (options["dry-run"]) {
      const result = { dryRun: true, files: entries.length, estimatedBytes: entries.reduce((sum, entry) => sum + entry.estimatedBytes, 0), output: path.resolve(options.output), includes: { data: Boolean(options["with-data"]), models: Boolean(options["with-models"]), reports: Boolean(options["with-reports"]) } };
      log(JSON.stringify(result, null, 2));
      return result;
    }
    log("Creating a private, unencrypted transfer. Use approved encrypted storage; credentials and SSO sessions are excluded.");
    const inventory = { ...await inspectDeployment({ repoRoot, homeDir, env, services: true }), ...codeMetadata(repoRoot) };
    const manifest = await exportTransfer({ ...transferOptions, outputDir: options.output, inventory, onProgress: log });
    log(`Transfer created with ${manifest.files.length} files. Keep the entire directory private; do not commit or upload it.`);
    return { files: manifest.files.length, output: path.resolve(options.output) };
  }
  if (!options["dry-run"]) {
    for (const port of [Number(env.VIZION_ANALYTICS_PORT || 3003), Number(env.VIZION_API_PORT || 3004)]) {
      if (await hasTcpServiceOnPort({ port })) throw new Error("Stop Vizion services and all data/model writers before importing. No process was stopped automatically.");
    }
  }
  const result = await importTransfer({ repoRoot, homeDir, inputDir: options.input, replace: options.replace, dryRun: options["dry-run"], onProgress: log });
  log(JSON.stringify(result, null, 2));
  return result;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  loadLocalEnv();
  try {
    const result = await runDeployment(process.argv.slice(2));
    if (result.ready === false) process.exitCode = 1;
  } catch (error) {
    console.error(`[deployment] ${error.message}`);
    process.exitCode = 1;
  }
}