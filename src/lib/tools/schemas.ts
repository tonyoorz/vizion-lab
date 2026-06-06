// OpenAI-compatible tool schemas. Sent to Lovable AI Gateway as `tools:[]`.
// Executed client-side in AIChat.executeTool().

export const TOOL_SCHEMAS = [
  {
    type: "function",
    function: {
      name: "list_tables",
      description: "List all DuckDB tables currently connected in the browser (name, row count, columns). Call first when you don't know what data is available.",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
  },
  {
    type: "function",
    function: {
      name: "profile_table",
      description: "Profile a DuckDB table: column types, null/distinct counts, min/max, top values. Call before writing the first SQL against an unfamiliar table.",
      parameters: {
        type: "object",
        properties: { table: { type: "string", description: "Table name" } },
        required: ["table"],
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "query_sql",
      description: "Execute a read-only DuckDB SQL query (SELECT/WITH/DESCRIBE/SHOW/PRAGMA/SUMMARIZE only). Quote identifiers with double quotes. Returns at most 200 rows.",
      parameters: {
        type: "object",
        properties: { sql: { type: "string", description: "Single read-only SQL statement, no trailing semicolon" } },
        required: ["sql"],
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "risk_scan",
      description: "Heuristic risk scan: null ratios, status imbalance, long-runners, time anomalies. Use to kick off 'insights / 风险' questions.",
      parameters: {
        type: "object",
        properties: { table: { type: "string", description: "Table name; omit to scan all" } },
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "run_python",
      description: "Execute Python in a sandboxed Pyodide worker. Pre-installed: numpy, pandas, scipy, scikit-learn, matplotlib. Listed tables are injected as pandas DataFrames with the same name. Use print() for outputs; plt.show() is captured as PNG. Use for advanced modeling beyond aggregate SQL.",
      parameters: {
        type: "object",
        properties: {
          code: { type: "string", description: "Python code" },
          tables: { type: "array", items: { type: "string" }, description: "DuckDB tables to inject as DataFrames" },
        },
        required: ["code"],
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "forecast",
      description: "Time-series forecast using Holt-Winters exponential smoothing (level + trend). Returns point forecast and 95% CI band. Use for trend prediction questions.",
      parameters: {
        type: "object",
        properties: {
          series: { type: "array", items: { type: "number" }, description: "Historical numeric values, ordered oldest to newest" },
          horizon: { type: "integer", description: "Steps ahead to forecast", minimum: 1, maximum: 60 },
          labels: { type: "array", items: { type: "string" }, description: "Optional time labels matching series" },
        },
        required: ["series", "horizon"],
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "detect_anomaly",
      description: "Detect outliers in a numeric series using z-score (>3σ) and IQR (1.5×). Returns indices, values, and method that flagged each.",
      parameters: {
        type: "object",
        properties: {
          series: { type: "array", items: { type: "number" } },
          labels: { type: "array", items: { type: "string" }, description: "Optional labels for each point" },
        },
        required: ["series"],
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "web_search",
      description: "Search the web for current information (best practices, public datasets, vendor docs). Use sparingly — prefer local data.",
      parameters: {
        type: "object",
        properties: { query: { type: "string" } },
        required: ["query"],
        additionalProperties: false,
      },
    },
  },
] as const;
