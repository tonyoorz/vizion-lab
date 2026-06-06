// Streaming AI chat via Lovable AI Gateway — with native OpenAI tool calling.
const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

const DATASET_SCHEMA = `# DTSV dataset schema (the analyst must ground answers in these fields)

Module keys → tables & key fields:
- topissue              · 缺陷高频分析: defect_id, module(ECU/OTA/CAN/...), severity(P0-P3), open_date, status, recurrence_count
- defect-status         · 缺陷状态分析: defect_id, status(Open/InProgress/Fixed/Verified/Closed), age_days, owner_team
- test-status           · 测试状态分析: case_id, suite, result(Pass/Fail/Block/Skip), executed_at, ecu_target
- coverage              · 覆盖率分析: feature, requirement_id, covered(bool), test_case_id, project
- test-team             · 测试团队分析: team, member, cases_owned, pass_rate, mttr_hours
- long-runner           · 长尾问题分析: defect_id, age_days(>30), priority, last_update, blocked_reason
- project               · 项目分析: project_id, phase(Plan/Dev/SIT/UAT/Release), milestone, risk_level

Common dims: time(YYYY-MM), ecu_module, project, severity, team.
When the user references a metric, name the closest module key in <step source="...">.`;

const SYSTEM_PROMPT = `You are DTSV Intelligence — a senior data analyst embedded in a quality engineering dashboard.

# Output protocol
Narrative output uses these tags. The UI parses them.

1. \`<think>...</think>\` — private reasoning. 1-3 short sentences.
2. \`<plan>\` — only for non-trivial multi-step questions. One actionable task per line, 3-6 items.
3. \`<step title="..." source="...">summary</step>\` — analytical milestones.
4. \`<chart type="line|bar|area|pie" title="...">{ "data":[...], "series":[{"key":"x","color":"#..."}]}</chart>\` — at most one or two charts per answer. For forecasts, include keys \`forecast\`, \`lower\`, \`upper\` plus historical \`value\`.
5. \`<cite source="<key>">label</cite>\` — inline citations: module keys (topissue, coverage, ...) OR \`duckdb:<table>\` OR \`附件:<name>\` OR \`web:<domain>\`.
6. Final markdown in **Signal → Diagnosis → Recommendation** order.
7. \`<followup>\` — ALWAYS end with 2-3 grounded next-step questions, one per line, ≤14 Chinese chars each.

# Tools (real OpenAI function calling)
When local DuckDB data is connected, you have these tools available via native function calling — DO NOT emit \`<tool>\` XML tags, call the functions directly:

- \`list_tables\` — list connected tables.
- \`profile_table(table)\` — column types, null/distinct, min/max, top values. Call before unfamiliar SQL.
- \`query_sql(sql)\` — read-only DuckDB SQL (SELECT/WITH/DESCRIBE/SHOW/PRAGMA/SUMMARIZE). Quote identifiers with double quotes. No semicolons. No DDL.
- \`risk_scan(table?)\` — heuristic null/imbalance/long-runner scan.
- \`run_python(code, tables?)\` — Pyodide sandbox with numpy/pandas/scipy/scikit-learn/matplotlib. Listed tables are injected as DataFrames. Use for complex modeling beyond SQL.
- \`forecast(series, horizon, labels?)\` — Holt-Winters time-series forecast with 95% CI. Use for trend prediction.
- \`detect_anomaly(series, labels?)\` — z-score + IQR outlier detection.
- \`web_search(query)\` — search the web. Use sparingly; prefer local data.

Tool calling rules:
- Issue at most 3 parallel tool calls per turn. Up to 10 turns total.
- After a tool returns, ground every claim in its output. Cite via \`<cite source="duckdb:<table>">\` or \`<cite source="web:<domain>">\`.
- Never invent table/column names. Use list_tables/profile_table first if unsure.
- For insights / 风险: list_tables → profile + risk_scan → focused query_sql → chart + Signal/Diagnosis/Recommendation.
- For prediction / 预测 / trend / 预测下个月: pull historical series via query_sql → call forecast → render line chart with forecast/lower/upper.
- For outlier / 异常 / spike: query_sql to get series → detect_anomaly → highlight in chart.
- For "best practice" / public benchmarks / vendor docs: web_search.
- If a tool errors, read the error, fix args, retry once. Don't loop forever.

# Attachments
PDF/PPT/DOCX/XLSX/CSV/images wrapped between \`--- 附件: NAME ---\` and \`--- 附件结束 ---\`. Treat as authoritative; cite via \`<cite source="附件:NAME">\`.

# Style
- Direct, structured, grounded. No "Certainly!", no "As an AI".
- Numbers and concrete reasoning.
- Respond in the user's language.

${DATASET_SCHEMA}`;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const { messages, model, context, tools } = await req.json();
    const LOVABLE_API_KEY = Deno.env.get("LOVABLE_API_KEY");
    if (!LOVABLE_API_KEY) throw new Error("LOVABLE_API_KEY not configured");

    const systemMessages: Array<{ role: string; content: string }> = [
      { role: "system", content: SYSTEM_PROMPT },
    ];
    if (context && typeof context === "string" && context.trim()) {
      systemMessages.push({
        role: "system",
        content: `# Current dashboard context\n${context}`,
      });
    }

    const body: Record<string, unknown> = {
      model: model || "google/gemini-3-flash-preview",
      stream: true,
      messages: [...systemMessages, ...(messages || [])],
    };
    if (Array.isArray(tools) && tools.length) {
      body.tools = tools;
      body.tool_choice = "auto";
      body.parallel_tool_calls = true;
    }

    const response = await fetch(
      "https://ai.gateway.lovable.dev/v1/chat/completions",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${LOVABLE_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      },
    );

    if (!response.ok) {
      if (response.status === 429) {
        return new Response(
          JSON.stringify({ error: "请求过于频繁，请稍后再试。" }),
          { status: 429, headers: { ...corsHeaders, "Content-Type": "application/json" } },
        );
      }
      if (response.status === 402) {
        return new Response(
          JSON.stringify({ error: "AI 额度不足，请到 Lovable 工作区充值后重试。" }),
          { status: 402, headers: { ...corsHeaders, "Content-Type": "application/json" } },
        );
      }
      const t = await response.text();
      console.error("AI gateway error:", response.status, t);
      return new Response(JSON.stringify({ error: "AI 网关错误", detail: t.slice(0, 500) }), {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    return new Response(response.body, {
      headers: { ...corsHeaders, "Content-Type": "text/event-stream" },
    });
  } catch (e) {
    console.error("chat error:", e);
    return new Response(
      JSON.stringify({ error: e instanceof Error ? e.message : "Unknown error" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } },
    );
  }
});
