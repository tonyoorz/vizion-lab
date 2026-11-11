import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const runtime = vi.hoisted(() => ({
  spawn: vi.fn(),
  spawnSync: vi.fn(),
  hasHealthyServiceOnPort: vi.fn(),
  hasTcpServiceOnPort: vi.fn(),
  hasViteDevServerOnPort: vi.fn(),
  waitForHealthyService: vi.fn(),
}));

vi.mock("node:child_process", () => {
  const childProcess = { spawn: runtime.spawn, spawnSync: runtime.spawnSync };
  return { ...childProcess, default: childProcess };
});

vi.mock("../../../server/loadLocalEnv.mjs", () => ({ loadLocalEnv: vi.fn() }));
vi.mock("../../../scripts/nodeVersion.mjs", () => ({ assertSupportedNodeVersion: vi.fn() }));
vi.mock("../../../scripts/devHelpers.mjs", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../../scripts/devHelpers.mjs")>(),
  resolveLocalApiEnvironment: (env: NodeJS.ProcessEnv) => env,
  hasHealthyServiceOnPort: runtime.hasHealthyServiceOnPort,
  hasTcpServiceOnPort: runtime.hasTcpServiceOnPort,
  hasViteDevServerOnPort: runtime.hasViteDevServerOnPort,
  waitForHealthyService: runtime.waitForHealthyService,
}));

describe("dev session keeper startup", () => {
  let child: EventEmitter & { pid: number; killed: boolean; kill: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    vi.stubEnv("SESSION_KEEPER_URL", "http://localhost:8090");
    vi.stubEnv("SESSION_STORE_DIR", undefined);
    vi.stubEnv("VIZION_ANALYTICS_PORT", "3003");
    vi.stubEnv("VIZION_API_PORT", "3004");
    vi.stubEnv("VIZION_WEB_PORT", "8080");
    vi.stubEnv("VIZION_WEB_HOST", "");
    vi.stubEnv("HOST", "");
    vi.spyOn(process, "on").mockReturnValue(process);
    vi.spyOn(process, "exit").mockImplementation((code) => {
      throw new Error(`exit ${code}`);
    });
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});

    child = Object.assign(new EventEmitter(), { pid: 4321, killed: false, kill: vi.fn() });
    runtime.spawn.mockReturnValue(child);
    runtime.hasHealthyServiceOnPort.mockResolvedValue(true);
    runtime.hasTcpServiceOnPort.mockResolvedValue(false);
    runtime.hasViteDevServerOnPort.mockResolvedValue(true);
    runtime.waitForHealthyService.mockResolvedValue(true);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("defaults to an isolated keeper on 18090 and passes its address to the analytics API", async () => {
    vi.stubEnv("SESSION_KEEPER_URL", undefined);
    runtime.hasHealthyServiceOnPort.mockImplementation(async ({ port }) => port !== 18090 && port !== 3003);

    await import("../../../scripts/dev.mjs");

    expect(runtime.spawn).toHaveBeenCalledTimes(2);
    expect(runtime.spawn.mock.calls[0][1]).toEqual([
      "-m", "uvicorn", "session_keeper.app:app", "--host", "127.0.0.1", "--port", "18090",
    ]);
    expect(runtime.spawn.mock.calls[1][1]).toContain("backend.analytics.api:app");
    for (const [, , options] of runtime.spawn.mock.calls) {
      expect(options.env.SESSION_KEEPER_URL).toBe("http://127.0.0.1:18090");
    }
    expect(runtime.hasHealthyServiceOnPort.mock.calls.some(([options]) => options.port === 8090)).toBe(false);
  });

  it("defaults session storage to this repository's ignored backend cache", async () => {
    await import("../../../scripts/dev.mjs");

    expect(process.env.SESSION_STORE_DIR).toBe(
      resolve("backend/cache/session-keeper"),
    );
  });

  it("preserves an explicitly configured session storage directory", async () => {
    vi.stubEnv("SESSION_STORE_DIR", "custom-session-cache");

    await import("../../../scripts/dev.mjs");

    expect(process.env.SESSION_STORE_DIR).toBe("custom-session-cache");
  });

  it("keeps LibreChat's 8090 out of the default restart cleanup ports", () => {
    const script = readFileSync(resolve("scripts/restart-dev.sh"), "utf8");

    expect(script).toContain("PORTS=(3004 8080 3003 18090)");
    expect(script).not.toMatch(/\b8090\b/);
  });

  it("starts the local keeper and waits for readiness before checking the analytics API", async () => {
    const events: string[] = [];
    runtime.hasHealthyServiceOnPort.mockImplementation(async ({ port }) => {
      events.push(`health:${port}`);
      return port !== 8090;
    });
    runtime.waitForHealthyService.mockImplementation(async (isHealthy) => {
      events.push("wait:keeper");
      expect(await isHealthy()).toBe(false);
      return true;
    });

    await import("../../../scripts/dev.mjs");

    expect(runtime.spawn).toHaveBeenCalledExactlyOnceWith(
      expect.any(String),
      ["-m", "uvicorn", "session_keeper.app:app", "--host", "127.0.0.1", "--port", "8090"],
      expect.objectContaining({ stdio: "inherit" }),
    );
    expect(events.indexOf("wait:keeper")).toBeGreaterThan(events.indexOf("health:8090"));
    expect(events.indexOf("health:3003")).toBeGreaterThan(events.indexOf("wait:keeper"));
  });

  it("binds the local frontend to all IPv4 interfaces for intranet access", async () => {
    runtime.hasViteDevServerOnPort.mockResolvedValue(false);

    await import("../../../scripts/dev.mjs");

    const viteCall = runtime.spawn.mock.calls.find(([, args]) => args.some((argument) => String(argument).endsWith("vite.js")));
    expect(viteCall?.[1]).toEqual(expect.arrayContaining(["--host", "0.0.0.0"]));
  });

  it("lets VIZION_WEB_HOST override the LibreChat-style HOST bind", async () => {
    vi.stubEnv("HOST", "0.0.0.0");
    vi.stubEnv("VIZION_WEB_HOST", "127.0.0.1");
    runtime.hasViteDevServerOnPort.mockResolvedValue(false);

    await import("../../../scripts/dev.mjs");

    const viteCall = runtime.spawn.mock.calls.find(([, args]) => args.some((argument) => String(argument).endsWith("vite.js")));
    expect(viteCall?.[1]).toEqual(expect.arrayContaining(["--host", "127.0.0.1"]));
  });

  it("reuses a healthy keeper without spawning or terminating it", async () => {
    await import("../../../scripts/dev.mjs");

    expect(runtime.hasHealthyServiceOnPort).toHaveBeenCalledWith({
      port: 8090,
      expectedService: "session-keeper",
    });
    expect(runtime.spawn).not.toHaveBeenCalled();
    const shutdown = vi.mocked(process.on).mock.calls.find(([signal]) => signal === "SIGINT")?.[1];
    expect(shutdown).toBeTypeOf("function");
    shutdown?.();
    expect(runtime.spawnSync).not.toHaveBeenCalled();
  });

  it("uses the port from a configured local keeper URL", async () => {
    vi.stubEnv("SESSION_KEEPER_URL", "http://127.0.0.1:18090");
    runtime.hasHealthyServiceOnPort.mockImplementation(async ({ port }) => port !== 18090);

    await import("../../../scripts/dev.mjs");

    expect(runtime.spawn).toHaveBeenCalledWith(
      expect.any(String),
      expect.arrayContaining(["session_keeper.app:app", "--port", "18090"]),
      expect.any(Object),
    );
  });

  it("does not launch a local keeper when a remote URL is configured", async () => {
    vi.stubEnv("SESSION_KEEPER_URL", "https://keeper.example.test");

    await import("../../../scripts/dev.mjs");

    expect(runtime.hasHealthyServiceOnPort.mock.calls.map(([options]) => options.port)).toEqual([3003, 3004]);
    expect(runtime.spawn).not.toHaveBeenCalled();
  });

  it("stops before launching APIs when the keeper port is occupied by an unhealthy service", async () => {
    runtime.hasHealthyServiceOnPort.mockResolvedValue(false);
    runtime.hasTcpServiceOnPort.mockResolvedValue(true);
    runtime.waitForHealthyService.mockResolvedValue(false);

    await expect(import("../../../scripts/dev.mjs")).rejects.toThrow("exit 1");

    expect(runtime.hasHealthyServiceOnPort).toHaveBeenCalledWith({
      port: 8090,
      expectedService: "session-keeper",
    });
    expect(runtime.spawn).not.toHaveBeenCalled();
    expect(runtime.hasHealthyServiceOnPort.mock.calls.some(([options]) => options.port === 3003)).toBe(false);
  });

  it("cleans up a newly started keeper if it never becomes healthy", async () => {
    runtime.hasHealthyServiceOnPort.mockImplementation(async ({ port }) => port !== 8090);
    runtime.waitForHealthyService.mockResolvedValue(false);

    await expect(import("../../../scripts/dev.mjs")).rejects.toThrow("exit 1");

    expect(runtime.spawn).toHaveBeenCalledTimes(1);
    expect(runtime.hasHealthyServiceOnPort.mock.calls.some(([options]) => options.port === 3003)).toBe(false);
    if (process.platform === "win32") {
      expect(runtime.spawnSync).toHaveBeenCalledWith("taskkill", ["/PID", "4321", "/T", "/F"], { stdio: "ignore" });
    } else {
      expect(child.kill).toHaveBeenCalledWith("SIGTERM");
    }
  });

  it("registers shutdown handlers before waiting for keeper readiness", async () => {
    runtime.hasHealthyServiceOnPort.mockImplementation(async ({ port }) => port !== 8090);
    runtime.waitForHealthyService.mockImplementation(async () => {
      expect(vi.mocked(process.on).mock.calls.map(([signal]) => signal)).toEqual(
        expect.arrayContaining(["SIGINT", "SIGTERM", "exit"]),
      );
      return true;
    });

    await import("../../../scripts/dev.mjs");

    expect(runtime.waitForHealthyService).toHaveBeenCalledTimes(1);
  });

  it.each(["health", "tcp", "existing-readiness", "new-readiness"])(
    "cancels pending startup when interrupted during %s",
    async (phase) => {
      let completeProbe!: (healthy: boolean) => void;
      let announceProbe!: () => void;
      const pendingProbe = new Promise<boolean>((resolve) => { completeProbe = resolve; });
      const probeStarted = new Promise<void>((resolve) => { announceProbe = resolve; });
      const deferredProbe = () => {
        announceProbe();
        return pendingProbe;
      };
      runtime.hasHealthyServiceOnPort.mockImplementation(async ({ port }) => port !== 8090);
      if (phase === "health") {
        runtime.hasHealthyServiceOnPort.mockImplementationOnce(deferredProbe);
      } else if (phase === "tcp") {
        runtime.hasTcpServiceOnPort.mockImplementationOnce(deferredProbe);
      } else {
        runtime.hasTcpServiceOnPort.mockResolvedValue(phase === "existing-readiness");
        runtime.waitForHealthyService.mockImplementationOnce(deferredProbe);
      }

      const startup = import("../../../scripts/dev.mjs");
      await probeStarted;
      const shutdown = vi.mocked(process.on).mock.calls.find(([signal]) => signal === "SIGINT")?.[1];
      expect(shutdown).toBeTypeOf("function");
      shutdown?.();
      completeProbe(phase === "new-readiness");
      await startup;

      expect(runtime.spawn).toHaveBeenCalledTimes(phase === "new-readiness" ? 1 : 0);
      expect(runtime.hasHealthyServiceOnPort.mock.calls.some(([options]) => options.port === 3003)).toBe(false);
      expect(process.exit).not.toHaveBeenCalled();
    },
  );
});