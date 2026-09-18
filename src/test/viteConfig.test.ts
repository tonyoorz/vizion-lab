// @vitest-environment node
import { describe, expect, it } from "vitest";

import viteConfig from "../../vite.config";

describe("Vite dependency optimization", () => {
  it("scans only the application HTML entry", () => {
    const config = viteConfig({
      command: "serve",
      mode: "development",
      isSsrBuild: false,
      isPreview: false,
    });

    expect(config.optimizeDeps?.entries).toEqual(["index.html"]);
  });

  it("ignores the local Python environment from file watching", () => {
    const config = viteConfig({
      command: "serve",
      mode: "development",
      isSsrBuild: false,
      isPreview: false,
    });

    expect(config.server?.watch).toMatchObject({
      ignored: expect.arrayContaining(["**/.venv/**"]),
    });
  });

  it("proxies authentication requests to the analytics API", () => {
    const config = viteConfig({
      command: "serve",
      mode: "development",
      isSsrBuild: false,
      isPreview: false,
    });

    expect(config.server?.proxy).toMatchObject({
      "/api/auth": {
        target: "http://127.0.0.1:3003",
        changeOrigin: true,
      },
    });
  });

  it("proxies signed agent export downloads to the analytics API", () => {
    const config = viteConfig({
      command: "serve",
      mode: "development",
      isSsrBuild: false,
      isPreview: false,
    });

    expect(config.server?.proxy).toMatchObject({
      "/api/agent/analytics/defects/export/download": {
        target: "http://127.0.0.1:3003",
        changeOrigin: true,
      },
    });
  });

  it("ignores generated review exports so audit artifacts do not reload the active page", () => {
    const config = viteConfig({
      command: "serve",
      mode: "development",
      isSsrBuild: false,
      isPreview: false,
    });

    expect(config.server?.watch).toMatchObject({
      ignored: expect.arrayContaining(["**/exports/**"]),
    });
  });
});