import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..", "..");

/**
 * MCP Client — Phase 1: Jira MCP over Streamable HTTP.
 *
 * Reads configuration from process.env (loaded by loadLocalEnv.mjs from .env):
 *   JIRA_MCP_URL          — MCP server endpoint (https://atc.bmwgroup.net/mcp)
 *   JIRA_PERSONAL_TOKEN   — Jira Personal Access Token
 *   JIRA_URL              — Jira instance base URL (https://jira.cc.bmwgroup.net)
 *
 * The BMW ATC MCP server authenticates via custom headers
 * (X-Atlassian-Jira-Personal-Token / X-Atlassian-Jira-Url) rather than OAuth.
 *
 * Implements the MCP Streamable HTTP transport with proper initialize handshake:
 *   1. POST initialize → server returns capabilities + Mcp-Session-Id header
 *   2. POST notifications/initialized (no response expected)
 *   3. POST tools/list, tools/call (with Mcp-Session-Id header)
 *
 * This module is the seed for the Phase 2 generic MCP client layer — the Jira
 * specifics are isolated in resolveJiraConfig / the header builders so the
 * generic callMcpHttp / listMcpTools / callMcpTool helpers can be lifted out
 * unchanged.
 */

const CLIENT_INFO = { name: "vizion-lab-aichat", version: "1.0.0" };
const PROTOCOL_VERSION = "2025-06-18";

// Per-server state keyed by server URL.
const serverState = new Map(); // url → { sessionId, initialized, toolList }

function getServerState(url) {
  if (!serverState.has(url)) {
    serverState.set(url, { sessionId: null, initialized: false, toolList: null });
  }
  return serverState.get(url);
}

let rpcCounter = 0;
function nextRpcId() {
  return ++rpcCounter;
}

function resolveJiraConfig(env = process.env) {
  const url = String(env.JIRA_MCP_URL || "").trim();
  const token = String(env.JIRA_PERSONAL_TOKEN || "").trim();
  const jiraUrl = String(env.JIRA_URL || "").trim();
  return { url, token, jiraUrl };
}

export function isJiraMcpConfigured(env = process.env) {
  const { url, token, jiraUrl } = resolveJiraConfig(env);
  return Boolean(url && token && jiraUrl);
}

function buildJiraHeaders(env = process.env) {
  const { token, jiraUrl } = resolveJiraConfig(env);
  return {
    "X-Atlassian-Jira-Personal-Token": token,
    "X-Atlassian-Jira-Url": jiraUrl,
  };
}

/**
 * Low-level JSON-RPC call over Streamable HTTP.
 *
 * MCP Streamable HTTP servers may respond with either a direct JSON body or an
 * SSE stream of `event: message\ndata: {...}` frames. This helper accepts both
 * and extracts the JSON-RPC result object.
 *
 * If expectResponse is false (used for notifications like
 * notifications/initialized), the request is fire-and-forget and null is returned.
 */
export async function callMcpHttp(serverUrl, headers, method, params = {}, options = {}) {
  const fetchFn = options.fetch || globalThis.fetch;
  const state = getServerState(serverUrl);
  const isNotification = method.startsWith("notifications/");
  const rpcId = isNotification ? undefined : nextRpcId();
  const body = JSON.stringify(
    isNotification
      ? { jsonrpc: "2.0", method, params }
      : { jsonrpc: "2.0", id: rpcId, method, params },
  );

  const requestHeaders = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    ...headers,
  };
  if (state.sessionId) {
    requestHeaders["Mcp-Session-Id"] = state.sessionId;
  }

  const response = await fetchFn(serverUrl, {
    method: "POST",
    headers: requestHeaders,
    body,
  });

  const newSessionId = response.headers.get("mcp-session-id");
  if (newSessionId) {
    state.sessionId = newSessionId;
  }

  // Notifications: no response body expected per MCP spec.
  if (isNotification) {
    return null;
  }

  if (!response.ok) {
    const errorText = await response.text().catch(() => "");
    throw Object.assign(new Error(`MCP HTTP ${response.status}: ${errorText.slice(0, 500)}`), {
      statusCode: response.status,
      retryable: response.status === 429 || response.status >= 500,
    });
  }

  const contentType = String(response.headers.get("content-type") || "");
  let payload;

  if (contentType.includes("text/event-stream")) {
    payload = await parseSseResponse(response);
  } else {
    const text = await response.text();
    payload = text ? JSON.parse(text) : null;
  }

  if (!payload) {
    throw new Error("MCP server returned an empty response");
  }
  if (payload.error) {
    throw Object.assign(new Error(`MCP error: ${payload.error.message || JSON.stringify(payload.error)}`), {
      code: payload.error.code,
      retryable: false,
    });
  }
  return payload.result;
}

/**
 * Parse an SSE text stream and return the first JSON-RPC result frame.
 * Frames look like:  event: message\r\ndata: {"jsonrpc":"2.0",...}\r\n\r\n
 */
async function parseSseResponse(response) {
  const text = await response.text();
  const dataLines = text
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());

  for (const line of dataLines) {
    if (!line) continue;
    try {
      const frame = JSON.parse(line);
      if (frame && (frame.result !== undefined || frame.error)) {
        return frame;
      }
    } catch {
      // skip non-JSON keepalive lines
    }
  }
  throw new Error("MCP SSE stream contained no JSON-RPC frame");
}

/**
 * Perform the MCP initialize handshake for a server (if not already done).
 *
 * Per MCP spec: send `initialize` with client info + protocol version, store
 * the session id from the response header, then send `notifications/initialized`
 * to complete the handshake. Subsequent calls carry the Mcp-Session-Id header.
 */
async function ensureInitialized(serverUrl, headers, options = {}) {
  const state = getServerState(serverUrl);
  if (state.initialized) {
    return state;
  }

  const result = await callMcpHttp(serverUrl, headers, "initialize", {
    protocolVersion: PROTOCOL_VERSION,
    capabilities: {},
    clientInfo: CLIENT_INFO,
  }, options);

  // Some servers may negotiate a different protocol version.
  if (result?.protocolVersion) {
    // Accept whatever the server offers.
  }

  // Complete the handshake with the initialized notification.
  await callMcpHttp(serverUrl, headers, "notifications/initialized", {}, options).catch(() => {
    // Some servers accept this as a normal JSON-RPC notification; others may
    // return 202 or an empty body. Ignore failures here — the session is
    // already established via the Mcp-Session-Id header from initialize.
  });

  state.initialized = true;
  return state;
}

/**
 * List tools exposed by the Jira MCP server (cached after first call).
 */
export async function listJiraMcpTools(options = {}) {
  const env = options.env || process.env;
  const { url } = resolveJiraConfig(env);
  if (!url) {
    throw Object.assign(new Error("JIRA_MCP_URL is not configured"), { code: "MCP_NOT_CONFIGURED" });
  }
  const headers = buildJiraHeaders(env);
  const state = getServerState(url);
  if (state.toolList && !options.refresh) {
    return state.toolList;
  }
  await ensureInitialized(url, headers, options);
  const result = await callMcpHttp(url, headers, "tools/list", {}, options);
  const tools = Array.isArray(result?.tools) ? result.tools : [];
  state.toolList = tools;
  return tools;
}

/**
 * Call a specific tool on the Jira MCP server by its original MCP tool name.
 */
export async function callJiraMcp(toolName, args = {}, options = {}) {
  const env = options.env || process.env;
  const { url } = resolveJiraConfig(env);
  if (!url) {
    throw Object.assign(new Error("JIRA_MCP_URL is not configured"), { code: "MCP_NOT_CONFIGURED" });
  }
  const headers = buildJiraHeaders(env);
  await ensureInitialized(url, headers, options);
  const result = await callMcpHttp(
    url,
    headers,
    "tools/call",
    { name: toolName, arguments: args },
    options,
  );

  // MCP tool results use { content: [{ type: "text", text: "..." }, ...], isError }
  if (result?.isError) {
    const errorText = extractMcpContentText(result);
    throw Object.assign(new Error(`MCP tool "${toolName}" returned an error: ${errorText.slice(0, 500)}`), {
      code: "MCP_TOOL_ERROR",
      retryable: false,
    });
  }
  return result;
}

/**
 * Find the best-matching MCP tool by keyword (used when the exact MCP tool name
 * is unknown — the Jira MCP server may name tools differently across versions).
 */
export async function resolveJiraMcpTool(keywords, options = {}) {
  const tools = await listJiraMcpTools(options);
  const pattern = new RegExp(keywords.join("|"), "i");
  // Prefer a name match, then a description match.
  return (
    tools.find((tool) => pattern.test(tool.name)) ||
    tools.find((tool) => pattern.test(tool.description || "")) ||
    null
  );
}

export function extractMcpContentText(result) {
  if (!result) return "";
  if (typeof result === "string") return result;
  const content = Array.isArray(result.content) ? result.content : [];
  return content
    .map((item) => {
      if (typeof item === "string") return item;
      if (item?.type === "text") return String(item.text || "");
      if (item?.text) return String(item.text);
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

/**
 * Summarize an MCP tool list into a compact text catalog for context injection.
 */
export function formatMcpToolCatalog(tools) {
  if (!Array.isArray(tools) || !tools.length) {
    return "(no tools available)";
  }
  return tools
    .map((tool) => {
      const desc = String(tool.description || "").slice(0, 200);
      const schema = tool.inputSchema ? JSON.stringify(tool.inputSchema) : "{}";
      return `- ${tool.name}: ${desc}\n  schema: ${schema}`;
    })
    .join("\n");
}

export function clearMcpCache() {
  serverState.clear();
}
