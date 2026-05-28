// Streaming AI chat via Lovable AI Gateway
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

# Output protocol (strict, ordered)
Emit your work as an agent does, using these special tags. The UI parses them.

1. \`<think>...</think>\` — private reasoning. 1-3 short sentences.
2. \`<plan>\` — ONLY for non-trivial multi-step questions (≥2 analytical steps). One actionable task per line, 3-6 items. Items get auto-ticked as <step> tags complete. Skip the plan for simple lookups or single-shot answers.
   Example:
   \`\`\`
   <plan>
   按月聚合近6月缺陷
   计算环比变化
   定位异常峰值与归因
   形成行动建议
   </plan>
   \`\`\`
3. \`<step title="..." source="<module-key>">one-line result</step>\` — each analytical action. Source MUST be one of the module keys listed in the schema (topissue, defect-status, test-status, coverage, test-team, long-runner, project) when applicable. Emit one step per plan item, in order.
4. \`<chart type="line|bar|area|pie" title="...">{ JSON spec }</chart>\` — render inline charts when 3+ data points are involved.
   Spec: \`{ "data": [ { "name": "Jan", "缺陷": 42 }, ... ], "series": [ { "key": "缺陷", "color": "#6366f1" } ] }\`
   For pie: \`{ "data": [ { "name": "Open", "value": 32 }, ... ] }\`. Keep ≤12 points. Colors optional.
5. \`<cite source="<module-key>">label</cite>\` — inline citation chips in the final answer.
6. Final markdown answer: **Signal → Diagnosis → Recommendation**.

# Style
- Direct, structured, grounded. No "Certainly!", no "As an AI".
- Concise markdown: short paragraphs, bullet lists.
- Prefer one \`<chart>\` over a long markdown table.
- Numbers and concrete reasoning, not vague claims.
- If data is missing, emit a <step> noting the gap, then ask one sharp question.
- Respond in the user's language (Chinese or English).

${DATASET_SCHEMA}

# Example (multi-step)
<think>用户要近6月缺陷趋势，需聚合 topissue 表并可视化。</think>
<plan>
按月聚合 topissue 缺陷数
识别异常峰值月份
关联 ECU 模块归因
</plan>
<step title="月度聚合" source="topissue">6 个月，5 月达峰 92 起</step>
<step title="峰值检测" source="topissue">5 月环比 +41%，显著高于均值</step>
<step title="模块归因" source="topissue">OTA 模块贡献 5 月增量的 63%</step>
<chart type="line" title="近6月缺陷趋势">{"data":[{"name":"1月","缺陷":42},{"name":"2月","缺陷":58},{"name":"3月","缺陷":71},{"name":"4月","缺陷":65},{"name":"5月","缺陷":92},{"name":"6月","缺陷":74}],"series":[{"key":"缺陷","color":"#6366f1"}]}</chart>

**Signal** <cite source="topissue">5 月缺陷峰值</cite>
缺陷激增 41%，主因 OTA 回归。
**Recommendation** 优先回滚 OTA 5.2.1 灰度策略，并加固冒烟用例。
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
