import { EventEmitter } from "node:events";
import path from "node:path";

import { describe, expect, it } from "vitest";

import * as devHelpers from "../../../scripts/devHelpers.mjs";

import {
  getTerminationCommand,
  hasHealthyServiceOnPort,
  hasTcpServiceOnPort,
  hasViteDevServerOnPort,
  waitForHealthyService,
} from "../../../scripts/devHelpers.mjs";

describe("resolvePythonCommand", () => {
  const repoRoot = path.resolve("test-workspace");

  it.each([
    ["win32", "Scripts", "python.exe"],
    ["darwin", "bin", "python"],
    ["linux", "bin", "python"],
  ])("selects the native virtual environment on %s", (platform, directory, executable) => {
    expect(devHelpers.resolvePythonCommand).toBeTypeOf("function");
    const expected = path.join(repoRoot, ".venv", directory, executable);
    expect(devHelpers.resolvePythonCommand({
      repoRoot,
      platform,
      env: {},
      existsSync: (candidate) => candidate === expected,
    })).toBe(expected);
  });

  it("preserves an existing explicit interpreter before the local environment", () => {
    expect(devHelpers.resolvePythonCommand).toBeTypeOf("function");
    const configured = path.join(repoRoot, "custom", "python");
    expect(devHelpers.resolvePythonCommand({
      repoRoot,
      platform: "darwin",
      env: { VIZION_ANALYTICS_PYTHON: configured },
      existsSync: () => true,
    })).toBe(configured);
  });

  it("skips stale interpreter paths copied from a different computer", () => {
    expect(devHelpers.resolvePythonCommand).toBeTypeOf("function");
    const nativePython = path.join(repoRoot, ".venv", "bin", "python");
    expect(devHelpers.resolvePythonCommand({
      repoRoot,
      platform: "darwin",
      env: { VIZION_ANALYTICS_PYTHON: "C:\\old-machine\\python.exe" },
      existsSync: (candidate) => candidate === nativePython,
    })).toBe(nativePython);
  });

  it.each([["win32", "python"], ["darwin", "python3"]])("uses the platform fallback on %s", (platform, executable) => {
    expect(devHelpers.resolvePythonCommand).toBeTypeOf("function");
    expect(devHelpers.resolvePythonCommand({
      repoRoot,
      platform,
      env: {},
      existsSync: () => false,
    })).toBe(executable);
  });
});

describe("resolveLocalApiEnvironment", () => {
  it("defaults an unset local API authentication mode to internal", () => {
    expect(devHelpers.resolveLocalApiEnvironment).toBeTypeOf("function");
    expect(devHelpers.resolveLocalApiEnvironment({})).toMatchObject({
      VIZION_AGENT_AUTH_MODE: "internal",
    });
  });

  it("adds an actor capability secret for the implicit local internal mode", () => {
    const localEnv = devHelpers.resolveLocalApiEnvironment({});

    expect(localEnv.VIZION_AGENT_ACTOR_CAPABILITY_SECRET).toEqual(expect.any(String));
    expect(localEnv.VIZION_AGENT_ACTOR_CAPABILITY_SECRET.trim()).not.toBe("");
  });

  it("adds the DTSV China row scope for the implicit local internal mode", () => {
    const localEnv = devHelpers.resolveLocalApiEnvironment({});

    expect(localEnv.VIZION_INTERNAL_TEAM_IDS).toBe("DTSV_China");
  });

  it("preserves an explicitly configured local internal row scope", () => {
    const localEnv = devHelpers.resolveLocalApiEnvironment({ VIZION_INTERNAL_PROJECT_IDS: "IDCEVO" });

    expect(localEnv.VIZION_INTERNAL_PROJECT_IDS).toBe("IDCEVO");
    expect(localEnv.VIZION_INTERNAL_TEAM_IDS).toBeUndefined();
  });

  it("adds the operations read policy for the implicit local internal mode", () => {
    const localEnv = devHelpers.resolveLocalApiEnvironment({});

    expect(localEnv.VIZION_INTERNAL_ROW_POLICY_IDS).toBe("agent.operations.read");
  });

  it("preserves an explicitly configured local internal row policy", () => {
    const localEnv = devHelpers.resolveLocalApiEnvironment({ VIZION_INTERNAL_ROW_POLICY_IDS: "quality-readonly" });

    expect(localEnv.VIZION_INTERNAL_ROW_POLICY_IDS).toBe("quality-readonly");
  });

  it("does not add a development actor capability secret to explicit OIDC mode", () => {
    const localEnv = devHelpers.resolveLocalApiEnvironment({ VIZION_AGENT_AUTH_MODE: "oidc" });

    expect(localEnv.VIZION_AGENT_ACTOR_CAPABILITY_SECRET).toBeUndefined();
    expect(localEnv.VIZION_INTERNAL_ROW_POLICY_IDS).toBeUndefined();
  });

  it("treats a blank local API authentication mode as unset", () => {
    expect(devHelpers.resolveLocalApiEnvironment({ VIZION_AGENT_AUTH_MODE: "   " })).toMatchObject({
      VIZION_AGENT_AUTH_MODE: "internal",
    });
  });

  it.each(["oidc", "internal"])("preserves the explicitly configured %s local API authentication mode", (authMode) => {
    expect(devHelpers.resolveLocalApiEnvironment({ VIZION_AGENT_AUTH_MODE: authMode })).toMatchObject({
      VIZION_AGENT_AUTH_MODE: authMode,
    });
  });
});

describe("resolvePythonInvocation", () => {
  it("uses the native Python resolver for standalone npm tools", () => {
    expect(devHelpers.resolvePythonInvocation).toBeTypeOf("function");
    const repoRoot = path.resolve("test-workspace");
    const python = path.join(repoRoot, ".venv", "bin", "python");
    expect(devHelpers.resolvePythonInvocation({
      repoRoot,
      platform: "darwin",
      env: {},
      args: ["-m", "backend.analytics_cli", "generate-qgate-kpi-reports"],
      existsSync: (candidate) => candidate === python,
    })).toMatchObject({
      command: python,
      args: ["-m", "backend.analytics_cli", "generate-qgate-kpi-reports"],
      cwd: repoRoot,
      env: { SESSION_KEEPER_URL: "http://127.0.0.1:18090", PYTHONUTF8: "1" },
    });
  });

  it("preserves an explicit external keeper for standalone tools", () => {
    expect(devHelpers.resolvePythonInvocation).toBeTypeOf("function");
    const invocation = devHelpers.resolvePythonInvocation({
      repoRoot: process.cwd(),
      env: { SESSION_KEEPER_URL: "https://keeper.example.test" },
      args: [],
      existsSync: () => false,
    });
    expect(invocation.env.SESSION_KEEPER_URL).toBe("https://keeper.example.test");
  });
});

describe("getTerminationCommand", () => {
  it("uses taskkill tree termination on Windows", () => {
    expect(getTerminationCommand("win32", 4321)).toEqual({
      command: "taskkill",
      args: ["/PID", "4321", "/T", "/F"],
    });
  });

  it("uses signal-based termination on non-Windows platforms", () => {
    expect(getTerminationCommand("linux", 4321, "SIGINT")).toEqual({
      command: null,
      args: ["SIGINT"],
    });
  });

  it("returns null for invalid pids", () => {
    expect(getTerminationCommand("win32", 0)).toBeNull();
  });
});

describe("hasViteDevServerOnPort", () => {
  it("returns true when the Vite root responds", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });

    await expect(
      hasViteDevServerOnPort({
        port: 8080,
        fetchImpl,
        timeoutMs: 0,
      }),
    ).resolves.toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith("http://127.0.0.1:8080/");
  });

  it("returns false when the Vite root is unavailable", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("connection refused"));

    await expect(
      hasViteDevServerOnPort({
        port: 8080,
        fetchImpl,
        timeoutMs: 0,
      }),
    ).resolves.toBe(false);
  });

  it("uses an abort signal when a timeout is configured", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });

    await expect(
      hasViteDevServerOnPort({
        port: 8080,
        fetchImpl,
        timeoutMs: 1000,
      }),
    ).resolves.toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://127.0.0.1:8080/",
      expect.objectContaining({ signal: expect.any(Object) }),
    );
  });
});

describe("hasHealthyServiceOnPort", () => {
  it.each([0, 2])("recognizes the session keeper health response with %i active sessions", async (activeSessions) => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok", active_sessions: activeSessions }),
    });

    await expect(
      hasHealthyServiceOnPort({
        port: 8090,
        expectedService: "session-keeper",
        fetchImpl,
        timeoutMs: 0,
      }),
    ).resolves.toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith("http://127.0.0.1:8090/health");
  });

  it.each([
    { ok: true, service: "analytics" },
    { status: "error", active_sessions: 0 },
    { status: "ok" },
    { status: "ok", active_sessions: -1 },
  ])("rejects an unrelated or unhealthy keeper response: %j", async (payload) => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    });

    await expect(
      hasHealthyServiceOnPort({
        port: 8090,
        expectedService: "session-keeper",
        fetchImpl,
        timeoutMs: 0,
      }),
    ).resolves.toBe(false);
  });

  it("returns true when the health endpoint reports ok", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, service: "analytics" }),
    });

    await expect(
      hasHealthyServiceOnPort({
        port: 3003,
        expectedService: "analytics",
        fetchImpl,
        timeoutMs: 0,
      }),
    ).resolves.toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith("http://127.0.0.1:3003/health");
  });

  it("returns false when the health payload does not match the expected service", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, service: "other" }),
    });

    await expect(
      hasHealthyServiceOnPort({
        port: 3003,
        expectedService: "analytics",
        fetchImpl,
        timeoutMs: 0,
      }),
    ).resolves.toBe(false);
  });

  it("returns false when the port is invalid", async () => {
    const fetchImpl = vi.fn();

    await expect(
      hasHealthyServiceOnPort({
        port: 0,
        fetchImpl,
        timeoutMs: 0,
      }),
    ).resolves.toBe(false);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("hasTcpServiceOnPort", () => {
  it("returns true when a TCP connection can be opened", async () => {
    const socket = new EventEmitter();
    socket.setTimeout = vi.fn();
    socket.destroy = vi.fn();
    const connectImpl = vi.fn(() => socket);

    const result = hasTcpServiceOnPort({ port: 3003, connectImpl });
    socket.emit("connect");

    await expect(result).resolves.toBe(true);
    expect(connectImpl).toHaveBeenCalledWith({ host: "127.0.0.1", port: 3003 });
    expect(socket.destroy).toHaveBeenCalled();
  });

  it("returns false when a TCP connection fails", async () => {
    const socket = new EventEmitter();
    socket.setTimeout = vi.fn();
    socket.destroy = vi.fn();
    const connectImpl = vi.fn(() => socket);

    const result = hasTcpServiceOnPort({ port: 3003, connectImpl });
    socket.emit("error", new Error("connection refused"));

    await expect(result).resolves.toBe(false);
    expect(socket.destroy).toHaveBeenCalled();
  });
});

describe("waitForHealthyService", () => {
  it("waits for a service to become healthy", async () => {
    const isHealthy = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);

    await expect(waitForHealthyService(isHealthy, { attempts: 3, delayMs: 0 })).resolves.toBe(true);
    expect(isHealthy).toHaveBeenCalledTimes(2);
  });

  it("returns false when the service never becomes healthy", async () => {
    const isHealthy = vi.fn().mockResolvedValue(false);

    await expect(waitForHealthyService(isHealthy, { attempts: 3, delayMs: 0 })).resolves.toBe(false);
    expect(isHealthy).toHaveBeenCalledTimes(3);
  });
});