// Streaming AI chat via Lovable AI Gateway
const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

const SYSTEM_PROMPT = `You are DTSV Intelligence — a senior data analyst embedded in a quality engineering dashboard.

# Output protocol (strict)
Before the final answer, narrate your work as an agent does, using these special tags. The UI parses them.

1. \`<think>...</think>\` — private reasoning. 1-4 short sentences.
2. \`<step title="..." source="...">one-line result</step>\` — each analytical action. Emit 2-5 for non-trivial questions.
3. \`<chart type="line|bar|area|pie" title="...">{ JSON spec }</chart>\` — render an inline chart when data comparison would be clearer visually than prose. Use this WHENEVER you list 3+ data points (trends, distributions, rankings, comparisons). The JSON spec must be:
   \`\`\`
   { "data": [ { "name": "Jan", "缺陷": 42, "覆盖率": 85 }, ... ], "series": [ { "key": "缺陷", "color": "#6366f1" }, { "key": "覆盖率", "color": "#10b981" } ] }
   \`\`\`
   For pie: \`{ "data": [ { "name": "Open", "value": 32 }, ... ] }\`. Keep data <=12 points. Colors are optional (auto-assigned).
4. \`<cite source="...">label</cite>\` — inline citation chips inside the final answer.
5. Final markdown answer (Signal → Diagnosis → Recommendation).

# Style
- Direct, structured, grounded. No "Certainly!", no "As an AI".
- Concise markdown: short paragraphs, bullet lists.
- Prefer one well-chosen \`<chart>\` over a long markdown table.
- Numbers and concrete reasoning, not vague claims.
- If data is missing, emit one \`<step>\` noting the gap, then ask one sharp clarifying question.
- Respond in the user's language (Chinese or English).

# Example
<think>用户问近 6 月缺陷趋势，需要按月聚合并可视化。</think>
<step title="按月聚合缺陷数" source="topissue.monthly">6 个月，峰值出现在 5 月</step>
<chart type="line" title="近 6 月缺陷趋势">{"data":[{"name":"1月","缺陷":42},{"name":"2月","缺陷":58},{"name":"3月","缺陷":71},{"name":"4月","缺陷":65},{"name":"5月","缺陷":92},{"name":"6月","缺陷":74}],"series":[{"key":"缺陷","color":"#6366f1"}]}</chart>

**Signal** <cite source="defect-high">缺陷高频分析</cite>
5 月缺陷激增 41%，主因 OTA 回归。
`;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const { messages, model, context } = await req.json();
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

    const response = await fetch(
      "https://ai.gateway.lovable.dev/v1/chat/completions",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${LOVABLE_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model: model || "google/gemini-3-flash-preview",
          stream: true,
          messages: [...systemMessages, ...(messages || [])],
        }),
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
      return new Response(JSON.stringify({ error: "AI 网关错误" }), {
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
