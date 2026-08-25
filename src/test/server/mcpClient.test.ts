// @vitest-environment node
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  isJiraMcpConfigured,
  extractMcpContentText,
  callMcpHttp,
  formatMcpToolCatalog,
  clearMcpCache,
} from "../../../server/mcpClient/index.mjs";

const mockResponse = (body, headers = {}) => ({
  ok: true,
  status: 200,
  headers: {
    get: (key) => headers[key.toLowerCase()] || null,
  },
  text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
});

describe("mcpClient", () => {
  beforeEach(() => {
    clearMcpCache();
  });

  describe("isJiraMcpConfigured", () => {
    it("returns false when no env vars are set", () => {
      expect(isJiraMcpConfigured({})).toBe(false);
    });

    it("returns true when all required vars are set", () => {
      expect(
        isJiraMcpConfigured({
          JIRA_MCP_URL: "https://atc.bmwgroup.net/mcp",
          JIRA_PERSONAL_TOKEN: "token123",
          JIRA_URL: "https://jira.cc.bmwgroup.net",
        }),
      ).toBe(true);
    });

    it("returns false when token is missing", () => {
      expect(
        isJiraMcpConfigured({
          JIRA_MCP_URL: "https://atc.bmwgroup.net/mcp",
          JIRA_URL: "https://jira.cc.bmwgroup.net",
        }),
      ).toBe(false);
    });
  });

  describe("extractMcpContentText", () => {
    it("extracts text from content array", () => {
      const result = {
        content: [
          { type: "text", text: "line 1" },
          { type: "text", text: "line 2" },
        ],
      };
      expect(extractMcpContentText(result)).toBe("line 1\nline 2");
    });

    it("returns empty string for null/undefined", () => {
      expect(extractMcpContentText(null)).toBe("");
      expect(extractMcpContentText(undefined)).toBe("");
    });

    it("returns string input as-is", () => {
      expect(extractMcpContentText("plain text")).toBe("plain text");
    });
  });

  describe("formatMcpToolCatalog", () => {
    it("formats tools into a compact catalog", () => {
      const tools = [
        { name: "search_issues", description: "Search Jira issues", inputSchema: { type: "object" } },
      ];
      const catalog = formatMcpToolCatalog(tools);
      expect(catalog).toContain("search_issues");
      expect(catalog).toContain("Search Jira issues");
    });

    it("returns placeholder for empty tool list", () => {
      expect(formatMcpToolCatalog([])).toBe("(no tools available)");
    });
  });

  describe("callMcpHttp", () => {
    it("sends JSON-RPC request and returns result", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({ jsonrpc: "2.0", id: 1, result: { tools: [{ name: "search" }] } }),
      );

      const result = await callMcpHttp(
        "https://example.com/mcp",
        { "X-Token": "abc" },
        "tools/list",
        {},
        { fetch: fetchMock },
      );

      expect(result).toEqual({ tools: [{ name: "search" }] });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [, init] = fetchMock.mock.calls[0];
      const body = JSON.parse(init.body);
      expect(body.method).toBe("tools/list");
      expect(body.jsonrpc).toBe("2.0");
      expect(init.headers["X-Token"]).toBe("abc");
    });

    it("parses SSE response frames", async () => {
      const sseBody = [
        'event: message',
        'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}',
        '',
        '',
      ].join("\r\n");

      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse(sseBody, { "content-type": "text/event-stream" }),
      );

      const result = await callMcpHttp(
        "https://example.com/mcp",
        {},
        "tools/list",
        {},
        { fetch: fetchMock },
      );

      expect(result).toEqual({ tools: [] });
    });

    it("throws on HTTP error", async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        headers: { get: () => null },
        text: async () => "Unauthorized",
      });

      await expect(
        callMcpHttp("https://example.com/mcp", {}, "tools/list", {}, { fetch: fetchMock }),
      ).rejects.toThrow("MCP HTTP 401");
    });

    it("throws on JSON-RPC error", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        mockResponse({ jsonrpc: "2.0", id: 1, error: { code: -32601, message: "Method not found" } }),
      );

      await expect(
        callMcpHttp("https://example.com/mcp", {}, "tools/call", {}, { fetch: fetchMock }),
      ).rejects.toThrow("MCP error: Method not found");
    });
  });
});
