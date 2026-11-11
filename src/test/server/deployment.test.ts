// @vitest-environment node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

const temporaryDirectories: string[] = [];
const createWorkspace = () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "vizion-deployment-"));
  temporaryDirectories.push(directory);
  fs.writeFileSync(path.join(directory, "package-lock.json"), "{}");
  fs.writeFileSync(path.join(directory, ".env.example"), "GLM_PROXY_API_KEY=\n");
  return directory;
};

afterEach(() => {
  vi.restoreAllMocks();
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

const loadDeployment = () => import("../../../scripts/deployment.mjs");
const loadTransfer = () => import("../../../scripts/deploymentTransfer.mjs");

describe("portable setup planning", () => {
  it.each([
    ["win32", "Scripts", "python.exe"],
    ["darwin", "bin", "python"],
  ])("creates a native virtual environment on %s", async (platform, directory, executable) => {
    const deployment = await loadDeployment();
    const repoRoot = createWorkspace();
    const steps = deployment.createSetupPlan({
      repoRoot,
      homeDir: repoRoot,
      platform,
      basePython: "/installed/python",
      npmCli: "/installed/npm-cli.js",
      env: {},
    });

    expect(steps.find((step) => step.id === "venv")).toMatchObject({
      command: "/installed/python",
      args: ["-m", "venv", path.join(repoRoot, ".venv")],
    });
    expect(steps.find((step) => step.id === "python-dependencies")).toMatchObject({
      command: path.join(repoRoot, ".venv", directory, executable),
      args: ["-m", "pip", "install", "-r", path.join(repoRoot, "requirements.txt")],
    });
    expect(steps.find((step) => step.id === "javascript-dependencies").args).toEqual([
      "/installed/npm-cli.js", "ci",
    ]);
    expect(steps.find((step) => step.id === "chromium").args).toEqual([
      "-m", "playwright", "install", "chromium",
    ]);
  });

  it("does not recreate an existing native environment", async () => {
    const deployment = await loadDeployment();
    const repoRoot = createWorkspace();
    const executable = path.join(repoRoot, ".venv", "Scripts", "python.exe");
    fs.mkdirSync(path.dirname(executable), { recursive: true });
    fs.writeFileSync(executable, "fixture");
    const steps = deployment.createSetupPlan({ repoRoot, homeDir: repoRoot, platform: "win32", npmCli: "/npm-cli.js", env: {} });
    expect(steps.some((step) => step.id === "venv")).toBe(false);
  });

  it("refuses a copied foreign or incomplete virtual environment", async () => {
    const deployment = await loadDeployment();
    const repoRoot = createWorkspace();
    fs.mkdirSync(path.join(repoRoot, ".venv", "Scripts"), { recursive: true });
    expect(() => deployment.createSetupPlan({ repoRoot, homeDir: repoRoot, platform: "darwin", npmCli: "/npm-cli.js", env: {} }))
      .toThrow(/foreign|incomplete/i);
  });

  it("installs the explicitly supplied keeper source with its real extras", async () => {
    const deployment = await loadDeployment();
    const repoRoot = createWorkspace();
    const keeperSource = path.join(repoRoot, "BmwLogin");
    fs.mkdirSync(keeperSource);
    const steps = deployment.createSetupPlan({ repoRoot, homeDir: repoRoot, npmCli: "/npm-cli.js", keeperSource, asr: true, env: {} });
    expect(steps.find((step) => step.id === "session-keeper").args).toEqual([
      "-m", "pip", "install", `${keeperSource}[all,session-keeper]`,
    ]);
    expect(steps.find((step) => step.id === "asr").args).toContain(path.join(repoRoot, "requirements-asr.txt"));
  });

  it("uses the local corporate pip helper without persisting proxy settings", async () => {
    const deployment = await loadDeployment();
    const repoRoot = createWorkspace();
    const pipHelper = path.join(repoRoot, "corp-pip-proxy.ps1");
    fs.writeFileSync(pipHelper, "fixture");
    const steps = deployment.createSetupPlan({ repoRoot, platform: "win32", npmCli: "/npm-cli.js", pipHelper, env: {} });
    const dependencies = steps.find((step) => step.id === "python-dependencies");
    expect(dependencies.command).toBe("powershell.exe");
    expect(dependencies.args).toContain(pipHelper);
    expect(dependencies.args).toContain("pip");
  });
});

describe("deployment readiness and redaction", () => {
  it("never emits URL credentials or query tokens", async () => {
    const deployment = await loadDeployment();
    expect(deployment.safeEndpoint("https://user:private@example.test/v1?token=secret#private"))
      .toBe("https://example.test/v1");
    expect(deployment.safeEndpoint("not-a-url-secret")).toBe("[configured endpoint]");
  });

  it("reports missing data and external tools without claiming full readiness", async () => {
    const deployment = await loadDeployment();
    const repoRoot = createWorkspace();
    const report = await deployment.inspectDeployment({
      repoRoot,
      homeDir: repoRoot,
      env: {},
      probe: { version: "3.13.9", modules: {}, chromium: false },
      commandAvailable: () => false,
    });
    expect(report.ready).toBe(false);
    expect(report.checks).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "python.module.session_keeper", status: "fail" }),
      expect.objectContaining({ id: "database.source", status: "fail" }),
      expect.objectContaining({ id: "chat.credential", status: "fail" }),
      expect.objectContaining({ id: "asr.ffmpeg", status: "fail" }),
    ]));
  });

  it("reports only configured key names, not secret values", async () => {
    const deployment = await loadDeployment();
    const repoRoot = createWorkspace();
    const secret = "never-print-this-credential";
    const report = await deployment.inspectDeployment({
      repoRoot,
      homeDir: repoRoot,
      env: { GLM_PROXY_API_KEY: secret, GLM_PROXY_BASE_URL: "http://127.0.0.1:8001/v1?key=hidden" },
      probe: { version: "3.13.9", modules: {}, chromium: false },
      commandAvailable: () => false,
    });
    const serialized = JSON.stringify(report);
    expect(serialized).not.toContain(secret);
    expect(serialized).not.toContain("key=hidden");
    expect(report.configuredKeys).toContain("GLM_PROXY_API_KEY");
  });

  it("does not overwrite an existing private environment file", async () => {
    const deployment = await loadDeployment();
    const repoRoot = createWorkspace();
    const filePath = path.join(repoRoot, ".env.local");
    fs.writeFileSync(filePath, "GLM_PROXY_API_KEY=private-value\n");
    expect(deployment.initializeLocalEnvironment(repoRoot)).toBe(false);
    expect(fs.readFileSync(filePath, "utf8")).toContain("private-value");
  });
});

describe("private transfer boundaries", () => {
  it("exports only a manifest by default, without secrets or private state", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    fs.writeFileSync(path.join(repoRoot, ".env.local"), "GLM_PROXY_API_KEY=never-export-this\n");
    fs.writeFileSync(path.join(repoRoot, "cookie.txt"), "private-cookie");
    const outputDir = path.join(repoRoot, "bundle");
    const manifest = await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, env: {} });
    expect(manifest.files).toEqual([]);
    expect(fs.readdirSync(outputDir)).toEqual(["manifest.json"]);
    expect(JSON.stringify(manifest)).not.toContain("never-export-this");
  });

  it("snapshots committed WAL content through SQLite backup, not raw file copy", async () => {
    const transfer = await loadTransfer();
    const { DatabaseSync } = await import("node:sqlite");
    const repoRoot = createWorkspace();
    const sourcePath = path.join(repoRoot, "database", "source", "qgate_raw.db");
    fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
    const source = new DatabaseSync(sourcePath);
    try {
      source.exec("PRAGMA journal_mode=WAL; CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT); INSERT INTO evidence(value) VALUES ('committed-wal-evidence');");
      expect(fs.existsSync(`${sourcePath}-wal`)).toBe(true);
      const outputDir = path.join(repoRoot, "bundle");
      const manifest = await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeData: true, env: {} });
      expect(manifest.files).toEqual([
        expect.objectContaining({ root: "repo", path: "database/source/qgate_raw.db", kind: "sqlite", sha256: expect.stringMatching(/^[a-f0-9]{64}$/) }),
      ]);
      const snapshot = new DatabaseSync(path.join(outputDir, "repo", "database", "source", "qgate_raw.db"), { readOnly: true });
      try {
        expect(snapshot.prepare("SELECT value FROM evidence").get().value).toBe("committed-wal-evidence");
      } finally {
        snapshot.close();
      }
    } finally {
      source.close();
    }
  });

  it("excludes sessions, logs, environment files and database sidecars", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const privatePaths = [
      "backend/cache/session-keeper/session.json",
      "backend/cache/embedding_models/.env",
      "database/source/qgate_raw.db-wal",
      "database/hot/logs/private.json",
      "exports/login_info.txt",
    ];
    for (const relative of privatePaths) {
      const filePath = path.join(repoRoot, relative);
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, "private");
    }
    const plan = transfer.planTransfer({ repoRoot, homeDir: repoRoot, includeData: true, includeModels: true, includeReports: true, env: {} });
    expect(plan).toEqual([]);
  });

  it("restores model caches into the destination home rather than the source user path", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    const destinationHome = createWorkspace();
    const modelPath = path.join(repoRoot, ".cache", "modelscope", "iic", "SenseVoiceSmall", "model.pt");
    fs.mkdirSync(path.dirname(modelPath), { recursive: true });
    fs.writeFileSync(modelPath, "synthetic-model-weights");
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeModels: true, env: {} });
    await transfer.importTransfer({ repoRoot: destination, homeDir: destinationHome, inputDir: outputDir });
    expect(fs.readFileSync(path.join(destinationHome, ".cache", "modelscope", "iic", "SenseVoiceSmall", "model.pt"), "utf8"))
      .toBe("synthetic-model-weights");
  });

  it("verifies every checksum before writing any destination file", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    fs.mkdirSync(path.join(repoRoot, "exports"));
    fs.writeFileSync(path.join(repoRoot, "exports", "first.json"), "{}");
    fs.writeFileSync(path.join(repoRoot, "exports", "second.json"), "{}");
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeReports: true, env: {} });
    fs.writeFileSync(path.join(outputDir, "repo", "exports", "second.json"), "tampered");
    await expect(transfer.importTransfer({ repoRoot: destination, homeDir: destination, inputDir: outputDir })).rejects.toThrow(/integrity|checksum/i);
    expect(fs.existsSync(path.join(destination, "exports", "first.json"))).toBe(false);
  });

  it("refuses overwriting existing private files by default", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    for (const directory of [repoRoot, destination]) fs.mkdirSync(path.join(directory, "exports"));
    fs.writeFileSync(path.join(repoRoot, "exports", "report.json"), "new");
    fs.writeFileSync(path.join(destination, "exports", "report.json"), "existing");
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeReports: true, env: {} });
    await expect(transfer.importTransfer({ repoRoot: destination, homeDir: destination, inputDir: outputDir })).rejects.toThrow(/already exists|overwrite/i);
    expect(fs.readFileSync(path.join(destination, "exports", "report.json"), "utf8")).toBe("existing");
  });

  it("backs up existing files before explicitly replacing them", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    for (const directory of [repoRoot, destination]) fs.mkdirSync(path.join(directory, "exports"));
    fs.writeFileSync(path.join(repoRoot, "exports", "report.json"), "new");
    fs.writeFileSync(path.join(destination, "exports", "report.json"), "existing");
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeReports: true, env: {} });
    const result = await transfer.importTransfer({ repoRoot: destination, homeDir: destination, inputDir: outputDir, replace: true });
    expect(fs.readFileSync(path.join(destination, "exports", "report.json"), "utf8")).toBe("new");
    expect(fs.readFileSync(path.join(result.backupDir, "repo", "exports", "report.json"), "utf8")).toBe("existing");
  });

  it.each(["../.env.local", "database/source/../../.env", "database/source/C:secret.db", "backend/cache/session-keeper/session.json"])("rejects an unsafe manifest path: %s", async (unsafePath) => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, env: {} });
    const manifestFile = path.join(outputDir, "manifest.json");
    const manifest = JSON.parse(fs.readFileSync(manifestFile, "utf8"));
    manifest.files = [{ root: "repo", path: unsafePath, bytes: 0, sha256: "a".repeat(64), kind: "file" }];
    fs.writeFileSync(manifestFile, JSON.stringify(manifest));
    await expect(transfer.importTransfer({ repoRoot, homeDir: repoRoot, inputDir: outputDir })).rejects.toThrow(/unsafe|allowlist|path/i);
  });

  it("refuses directory links that escape the allowlisted source", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const outside = createWorkspace();
    fs.writeFileSync(path.join(outside, "private.db"), "must-not-export");
    fs.symlinkSync(outside, path.join(repoRoot, "database"), process.platform === "win32" ? "junction" : "dir");
    expect(() => transfer.planTransfer({ repoRoot, homeDir: repoRoot, includeData: true, env: {} })).toThrow(/link/i);
  });

  it("can fully validate an import without writing destination files", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    fs.mkdirSync(path.join(repoRoot, "exports"));
    fs.writeFileSync(path.join(repoRoot, "exports", "report.json"), "{}");
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeReports: true, env: {} });
    const result = await transfer.importTransfer({ repoRoot: destination, homeDir: destination, inputDir: outputDir, dryRun: true });
    expect(result).toMatchObject({ validated: 1, dryRun: true });
    expect(fs.existsSync(path.join(destination, "exports"))).toBe(false);
    expect(fs.existsSync(path.join(destination, ".deployment"))).toBe(false);
  });
});

describe("deployment command boundaries", () => {
  const loadCommand = () => import("../../../scripts/deploy.mjs");

  it("rejects unknown commands and options before doing any work", async () => {
    const command = await loadCommand();
    expect(() => command.parseDeploymentArguments(["upload"])).toThrow(/command/i);
    expect(() => command.parseDeploymentArguments(["export", "--secrets"])).toThrow();
    expect(() => command.parseDeploymentArguments(["doctor", "--replace"])).toThrow(/option/i);
  });

  it("requires explicit transfer locations", async () => {
    const command = await loadCommand();
    expect(() => command.parseDeploymentArguments(["export", "--with-data"])).toThrow(/output/i);
    expect(() => command.parseDeploymentArguments(["import"])).toThrow(/input/i);
  });

  it("does not run installation or create private files for setup dry-run", async () => {
    const command = await loadCommand();
    const repoRoot = createWorkspace();
    const result = await command.runDeployment(["setup", "--dry-run"], {
      repoRoot,
      env: { npm_execpath: "/npm-cli.js" },
      log: () => {},
    });
    expect(result.dryRun).toBe(true);
    expect(fs.existsSync(path.join(repoRoot, ".venv"))).toBe(false);
    expect(fs.existsSync(path.join(repoRoot, ".env.local"))).toBe(false);
  });
});

describe("transfer preflight safety", () => {
  it("includes committed WAL bytes in the export capacity check", async () => {
    const transfer = await loadTransfer();
    const { DatabaseSync } = await import("node:sqlite");
    const repoRoot = createWorkspace();
    const sourcePath = path.join(repoRoot, "database", "source", "qgate_raw.db");
    fs.mkdirSync(path.dirname(sourcePath), { recursive: true });
    const source = new DatabaseSync(sourcePath);
    try {
      source.exec("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; CREATE TABLE evidence (value TEXT);");
      source.prepare("INSERT INTO evidence VALUES (?)").run("evidence".repeat(40000));
      const outputDir = path.join(repoRoot, "bundle");
      await expect(transfer.exportTransfer({
        repoRoot, homeDir: repoRoot, outputDir, includeData: true, env: {},
        spaceInfo: () => ({ volume: "fixture", availableBytes: fs.statSync(sourcePath).size * 2 }),
      })).rejects.toThrow(/space/i);
      expect(fs.existsSync(outputDir)).toBe(false);
    } finally {
      source.close();
    }
  });

  it("budgets replacement backups before changing any target", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    for (const directory of [repoRoot, destination]) fs.mkdirSync(path.join(directory, "exports"));
    fs.writeFileSync(path.join(repoRoot, "exports", "report.json"), "new");
    const original = "existing".repeat(40000);
    fs.writeFileSync(path.join(destination, "exports", "report.json"), original);
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeReports: true, env: {} });
    await expect(transfer.importTransfer({
      repoRoot: destination, homeDir: destination, inputDir: outputDir, replace: true,
      spaceInfo: () => ({ volume: "fixture", availableBytes: 100000 }),
    })).rejects.toThrow(/space/i);
    expect(fs.readFileSync(path.join(destination, "exports", "report.json"), "utf8")).toBe(original);
  });

  it("budgets home-file backups on the repository volume", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    const destinationHome = createWorkspace();
    const relative = ".cache/modelscope/iic/SenseVoiceSmall/model.pt";
    for (const directory of [repoRoot, destinationHome]) fs.mkdirSync(path.dirname(path.join(directory, relative)), { recursive: true });
    fs.writeFileSync(path.join(repoRoot, relative), "new-weights");
    fs.writeFileSync(path.join(destinationHome, relative), "existing".repeat(40000));
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeModels: true, env: {} });
    await expect(transfer.importTransfer({
      repoRoot: destination, homeDir: destinationHome, inputDir: outputDir, replace: true,
      spaceInfo: (directory) => directory === destinationHome
        ? { volume: "home", availableBytes: 1000000 }
        : { volume: "repository", availableBytes: 100000 },
    })).rejects.toThrow(/space/i);
    expect(fs.readFileSync(path.join(destinationHome, relative), "utf8")).toContain("existing");
  });

  it("refuses silently replacing an existing adapter inventory", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, env: {} });
    fs.mkdirSync(path.join(destination, ".deployment"));
    const inventoryPath = path.join(destination, ".deployment", "source-inventory.json");
    fs.writeFileSync(inventoryPath, '{"keeperTools":["codebeamer"]}');
    await expect(transfer.importTransfer({ repoRoot: destination, homeDir: destination, inputDir: outputDir })).rejects.toThrow(/inventory|already exists/i);
    expect(fs.readFileSync(inventoryPath, "utf8")).toContain("codebeamer");
  });

  it("backs up adapter inventory when replacement is explicitly allowed", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, env: {} });
    fs.mkdirSync(path.join(destination, ".deployment"));
    fs.writeFileSync(path.join(destination, ".deployment", "source-inventory.json"), '{"keeperTools":["codebeamer"]}');
    const result = await transfer.importTransfer({ repoRoot: destination, homeDir: destination, inputDir: outputDir, replace: true });
    expect(result.backupDir).toBeTypeOf("string");
    expect(fs.readFileSync(path.join(result.backupDir, "repo", ".deployment", "source-inventory.json"), "utf8")).toContain("codebeamer");
  });

  it("rejects dangling database sidecar links", async () => {
    const transfer = await loadTransfer();
    const { DatabaseSync } = await import("node:sqlite");
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    const relative = "database/source/qgate_raw.db";
    for (const directory of [repoRoot, destination]) fs.mkdirSync(path.dirname(path.join(directory, relative)), { recursive: true });
    const source = new DatabaseSync(path.join(repoRoot, relative));
    source.exec("CREATE TABLE evidence (id INTEGER);");
    source.close();
    fs.copyFileSync(path.join(repoRoot, relative), path.join(destination, relative));
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeData: true, env: {} });
    fs.symlinkSync(path.join(destination, "missing-sidecar-target"), `${path.join(destination, relative)}-wal`, process.platform === "win32" ? "junction" : "dir");
    await expect(transfer.importTransfer({ repoRoot: destination, homeDir: destination, inputDir: outputDir, replace: true })).rejects.toThrow(/sidecar|link/i);
  });

  it("verifies all staged bytes before committing any destination file", async () => {
    const transfer = await loadTransfer();
    const repoRoot = createWorkspace();
    const destination = createWorkspace();
    for (const directory of [repoRoot, destination]) fs.mkdirSync(path.join(directory, "exports"));
    for (const name of ["first", "second"]) {
      fs.writeFileSync(path.join(repoRoot, "exports", `${name}.json`), `new-${name}`);
      fs.writeFileSync(path.join(destination, "exports", `${name}.json`), `existing-${name}`);
    }
    const outputDir = path.join(repoRoot, "bundle");
    await transfer.exportTransfer({ repoRoot, homeDir: repoRoot, outputDir, includeReports: true, env: {} });
    const first = path.join(outputDir, "repo", "exports", "first.json");
    const second = path.join(outputDir, "repo", "exports", "second.json");
    const copyFile = fs.copyFileSync;
    vi.spyOn(fs, "copyFileSync").mockImplementation((source, target, mode) => {
      copyFile(source, target, mode);
      if (source === first) fs.writeFileSync(second, "bad-second");
    });
    await expect(transfer.importTransfer({ repoRoot: destination, homeDir: destination, inputDir: outputDir, replace: true })).rejects.toThrow(/integrity|checksum/i);
    expect(fs.readFileSync(path.join(destination, "exports", "first.json"), "utf8")).toBe("existing-first");
    expect(fs.readFileSync(path.join(destination, "exports", "second.json"), "utf8")).toBe("existing-second");
  });
});