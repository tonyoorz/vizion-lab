import { useState } from "react";
import { Sparkle, Send, X, BarChart3, Database, Settings2 } from "lucide-react";
import AgentStepCard, { AgentStep } from "./AgentStepCard";
import RetrievalStepCard from "./RetrievalStepCard";
import KnowledgeIngestionPanel from "./KnowledgeIngestionPanel";
import OntologyEditor from "./OntologyEditor";
import { ONTOLOGY, resolveQuestion, summarizeMatchForPrompt, listAllowedFields } from "@/lib/ontology";
import "@/lib/ontology/store"; // hydrate localStorage overrides at boot
import { duckdbManager } from "@/lib/duckdb/client";
import { isSafeReadOnlySql } from "@/lib/duckdb/safety";
import { searchKnowledge, summarizeHitsForPrompt, type KnowledgeHit } from "@/lib/rag/client";

interface Props {
  open: boolean;
  onClose: () => void;
  initialQuestion?: string;
}

const PRESETS = [
  "上周 P0 高频缺陷集中在哪个模块？",
  "近30天测试通过率最低的5个 ECU",
  "长尾缺陷（age>30天）按团队分布",
  "本月平均执行耗时趋势",
];

const callAgentStep = async (
  role: string,
  systemPrompt: string,
  userPrompt: string,
  opts: { jsonMode?: boolean; model?: string } = {},
): Promise<string> => {
  const resp = await fetch(
    `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/agent-step`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
      },
      body: JSON.stringify({ role, systemPrompt, userPrompt, ...opts }),
    },
  );
  const data = await resp.json();
  if (!resp.ok) throw new Error(data?.error || `${role} 调用失败`);
  return data.content as string;
};

const extractJSON = (raw: string): unknown => {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidate = (fenced ? fenced[1] : raw).trim();
  return JSON.parse(candidate);
};

const extractSQL = (raw: string): string => {
  const fenced = raw.match(/```sql\s*([\s\S]*?)```/i);
  if (fenced) return fenced[1].trim();
  const generic = raw.match(/```\s*([\s\S]*?)```/);
  if (generic) return generic[1].trim();
  return raw.trim();
};

const initialSteps = (): AgentStep[] => [
  { key: "ontology", name: "Ontology Resolver", hint: "本体匹配", status: "pending" },
  { key: "planner", name: "Planner", hint: "任务拆解", status: "pending" },
  { key: "retriever", name: "Retriever", hint: "Hybrid RAG 召回", status: "pending" },
  { key: "ontologist", name: "Ontologist", hint: "语义映射", status: "pending" },
  { key: "sql", name: "SQL Writer", hint: "受约束SQL生成", status: "pending" },
  { key: "exec", name: "Executor", hint: "DuckDB 本地执行", status: "pending" },
  { key: "critic", name: "Critic", hint: "结果合理性校验", status: "pending" },
  { key: "presenter", name: "Presenter", hint: "答案与图表生成", status: "pending" },
];

const AgentOrchestrator = ({ open, onClose, initialQuestion = "" }: Props) => {
  const [question, setQuestion] = useState(initialQuestion);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<AgentStep[]>(initialSteps());
  const [answer, setAnswer] = useState<string>("");
  const [chartSpec, setChartSpec] = useState<unknown>(null);
  const [kbOpen, setKbOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);

  const updateStep = (key: string, patch: Partial<AgentStep>) =>
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

  const run = async () => {
    if (!question.trim() || running) return;
    setRunning(true);
    setAnswer("");
    setChartSpec(null);
    setSteps(initialSteps());

    try {
      // ---------- 1. Ontology Resolver (local, no LLM) ----------
      const t0 = performance.now();
      updateStep("ontology", { status: "running" });
      const match = resolveQuestion(question);
      const matchSummary = summarizeMatchForPrompt(match);
      updateStep("ontology", {
        status: "done",
        summary: `${match.entities.length} 实体 / ${match.metrics.length} 指标 / ${match.timeHints.length} 时间`,
        durationMs: Math.round(performance.now() - t0),
        detail: (
          <pre className="whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
            {matchSummary}
          </pre>
        ),
      });

      const ontologyContext = `# Ontology (business semantic layer)
Entities:
${ONTOLOGY.entities
  .map(
    (e) =>
      `- ${e.name} → table ${e.table} (pk=${e.primaryKey}); attrs: ${e.attributes
        .map((a) => `${a.name}(${a.type})`)
        .join(", ")}`,
  )
  .join("\n")}

Metrics:
${ONTOLOGY.metrics
  .map((m) => `- ${m.name} on ${m.baseEntity}: ${m.formula} | dims: ${m.dimensions.join(",")}`)
  .join("\n")}

# Question
${question}

# Resolver pre-matches
${matchSummary}`;

      // ---------- 2. Planner ----------
      const t1 = performance.now();
      updateStep("planner", { status: "running" });
      const planRaw = await callAgentStep(
        "planner",
        "你是数据问答系统的 Planner。把用户问题拆解为 3-6 步可执行子任务，只输出 JSON: {plan: string[], needs_critic: boolean}。语言简洁、动词开头。",
        ontologyContext,
        { jsonMode: true },
      );
      const plan = extractJSON(planRaw) as { plan: string[]; needs_critic?: boolean };
      updateStep("planner", {
        status: "done",
        summary: `${plan.plan.length} 步`,
        durationMs: Math.round(performance.now() - t1),
        detail: (
          <ol className="list-decimal space-y-1 pl-4 text-muted-foreground">
            {plan.plan.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ol>
        ),
      });

      // ---------- 3. Retriever (Hybrid RAG) ----------
      const tR = performance.now();
      updateStep("retriever", { status: "running" });
      let hits: KnowledgeHit[] = [];
      let retrievalContext = "(知识库无召回)";
      try {
        const r = await searchKnowledge(question, { topK: 6 });
        hits = r.hits ?? [];
        retrievalContext = summarizeHitsForPrompt(hits);
        updateStep("retriever", {
          status: "done",
          summary: hits.length
            ? `${hits.length} 命中 · embed ${r.embed_ms}ms · search ${r.search_ms}ms`
            : "知识库为空，跳过",
          durationMs: Math.round(performance.now() - tR),
          detail: <RetrievalStepCard hits={hits} />,
        });
      } catch (err) {
        updateStep("retriever", {
          status: "error",
          error: (err as Error).message,
          summary: "检索失败，继续主链路",
          durationMs: Math.round(performance.now() - tR),
        });
      }

      // ---------- 4. Ontologist ----------
      const t2 = performance.now();
      updateStep("ontologist", { status: "running" });
      const mapRaw = await callAgentStep(
        "ontologist",
        `你是 Ontologist。基于提供的本体、用户问题和知识库召回，输出最终业务概念映射 JSON:
{
  "entities": ["..."],
  "metrics": ["..."],
  "dimensions": ["..."],
  "filters": [{"field":"defects.severity","op":"=","value":"P0"}],
  "time_range_days": 7
}
只能引用本体中存在的实体/字段/指标。`,
        `${ontologyContext}\n\n# Retrieved knowledge\n${retrievalContext}`,
        { jsonMode: true },
      );
      let mapping: any = {};
      try { mapping = extractJSON(mapRaw); } catch { mapping = { raw: mapRaw }; }
      updateStep("ontologist", {
        status: "done",
        summary: `映射 ${(mapping.metrics?.length ?? 0) + (mapping.dimensions?.length ?? 0)} 概念`,
        durationMs: Math.round(performance.now() - t2),
        detail: (
          <pre className="whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
            {JSON.stringify(mapping, null, 2)}
          </pre>
        ),
      });

      // ---------- 4. SQL Writer ----------
      const t3 = performance.now();
      updateStep("sql", { status: "running" });
      const allowed = listAllowedFields(match).join(", ") || "(no resolver hits — use any ontology field)";
      const sqlRaw = await callAgentStep(
        "sql_writer",
        `你是 SQL Writer。基于 Ontology + Mapping 生成一段 DuckDB SQL。
要求:
- 只读 SELECT (禁止 INSERT/UPDATE/DELETE/ATTACH 等)；
- 只能用本体中声明的表与字段；
- 限制 LIMIT 500；
- 仅输出 \`\`\`sql 代码块，无其他文字。`,
        `${ontologyContext}\n\n# Mapping\n${JSON.stringify(mapping)}\n\n# Allowed fields\n${allowed}\n\n# Retrieved knowledge\n${retrievalContext}`,
        { model: "google/gemini-3.5-flash" },
      );
      const sql = extractSQL(sqlRaw);
      const safe = isSafeReadOnlySql(sql);
      updateStep("sql", {
        status: safe ? "done" : "error",
        error: safe ? undefined : "安全校验失败：仅允许只读 SQL（SELECT/WITH/…）",
        summary: sql.split("\n")[0].slice(0, 80) + (sql.length > 80 ? "…" : ""),
        durationMs: Math.round(performance.now() - t3),
        detail: (
          <pre className="overflow-x-auto rounded bg-muted/40 p-2 font-mono text-[11px]">
            {sql}
          </pre>
        ),
      });
      if (!safe) throw new Error("SQL 未通过只读安全校验");

      // ---------- 5. Executor ----------
      const t4 = performance.now();
      updateStep("exec", { status: "running" });
      let result: any = null;
      let execError: string | undefined;
      try {
        result = await duckdbManager.runQuery(sql, 500);
      } catch (e: any) {
        execError = e?.message || String(e);
      }
      updateStep("exec", {
        status: execError ? "error" : "done",
        error: execError,
        summary: result
          ? `${result.rowCount} 行 × ${result.columns.length} 列`
          : execError
          ? "执行失败"
          : "无数据",
        durationMs: Math.round(performance.now() - t4),
        detail: result && (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[11px]">
              <thead>
                <tr className="bg-muted/40">
                  {result.columns.map((c: string) => (
                    <th key={c} className="border border-border/40 px-2 py-1 text-left">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 20).map((r: Record<string, unknown>, i: number) => (
                  <tr key={i}>
                    {result.columns.map((c: string) => (
                      <td key={c} className="border border-border/40 px-2 py-1">
                        {String(r[c] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ),
      });
      if (execError) {
        updateStep("critic", { status: "skipped" });
        updateStep("presenter", { status: "skipped" });
        return;
      }

      // ---------- 6. Critic ----------
      const t5 = performance.now();
      const needsCritic = plan.needs_critic !== false;
      if (!needsCritic) {
        updateStep("critic", { status: "skipped", summary: "Planner 跳过" });
      } else {
        updateStep("critic", { status: "running" });
        const sample = JSON.stringify(result.rows.slice(0, 10));
        const critRaw = await callAgentStep(
          "critic",
          `你是 Critic。审视用户问题、SQL、结果样本。输出 JSON: {verdict:"ok"|"redo", reason:string, hint?:string}。
判断标准: (a) 是否回答了问题; (b) 结果数量是否反常 (0 或异常爆炸); (c) 字段是否对得上。
若 verdict=redo，给出修正提示。`,
          `Q: ${question}\nSQL: ${sql}\nROWS: ${result.rowCount}\nSAMPLE: ${sample}`,
          { jsonMode: true },
        );
        let critic: any = {};
        try { critic = extractJSON(critRaw); } catch { critic = { verdict: "ok", reason: "(parse failed)" }; }
        updateStep("critic", {
          status: "done",
          summary: `${critic.verdict} · ${critic.reason?.slice(0, 60) ?? ""}`,
          durationMs: Math.round(performance.now() - t5),
          detail: (
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
              {JSON.stringify(critic, null, 2)}
            </pre>
          ),
        });
      }

      // ---------- 7. Presenter ----------
      const t6 = performance.now();
      updateStep("presenter", { status: "running" });
      const presentRaw = await callAgentStep(
        "presenter",
        `你是 Presenter。基于查询结果生成给用户的最终回答 JSON:
{
  "markdown": "结论 + 信号 + 建议，简洁，可引用具体数字",
  "chart": null | { "type":"bar"|"line"|"pie", "title":"...", "x":"<col>", "y":"<col>" }
}
图表只在结果适合可视化(>=2行)时给出。`,
        `Q: ${question}\nCOLUMNS: ${result.columns.join(",")}\nROWS(${result.rowCount}): ${JSON.stringify(result.rows.slice(0, 30))}`,
        { jsonMode: true },
      );
      let present: any = {};
      try { present = extractJSON(presentRaw); } catch { present = { markdown: presentRaw }; }
      setAnswer(present.markdown || "");
      if (present.chart) setChartSpec({ ...present.chart, rows: result.rows });
      updateStep("presenter", {
        status: "done",
        summary: "已生成回答",
        durationMs: Math.round(performance.now() - t6),
      });
    } catch (e: any) {
      const running = steps.find((s) => s.status === "running");
      if (running) updateStep(running.key, { status: "error", error: e?.message || String(e) });
      else setAnswer(`运行出错: ${e?.message || String(e)}`);
    } finally {
      setRunning(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 backdrop-blur-sm">
      <div className="relative mt-12 w-full max-w-3xl rounded-xl border border-border bg-background shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Sparkle className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">智能问数 · Multi-Agent</h2>
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              Ontology v{ONTOLOGY.version}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setEditorOpen(true)}
              className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
              title="编辑本体（实体/字段/指标）"
            >
              <Settings2 className="h-3 w-3" />
              本体
            </button>
            <button
              onClick={() => setKbOpen(true)}
              className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
              title="管理知识库 (Hybrid RAG)"
            >
              <Database className="h-3 w-3" />
              知识库
            </button>
            <button
              onClick={onClose}
              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="max-h-[75vh] overflow-y-auto p-5">
          {/* Input */}
          <div className="mb-4">
            <div className="flex items-center gap-2 rounded-lg border border-border bg-card/40 p-2">
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), run())}
                disabled={running}
                placeholder="例如：上周 P0 高频缺陷集中在哪个模块？"
                className="flex-1 bg-transparent px-2 text-[13px] outline-none placeholder:text-muted-foreground/60"
              />
              <button
                onClick={run}
                disabled={running || !question.trim()}
                className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                <Send className="h-3.5 w-3.5" />
                {running ? "运行中" : "提问"}
              </button>
            </div>
            {!running && !answer && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {PRESETS.map((p) => (
                  <button
                    key={p}
                    onClick={() => setQuestion(p)}
                    className="rounded-full border border-border bg-card/40 px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    {p}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Agent workflow */}
          <div className="mb-4">
            <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Agent 工作流
            </div>
            <div className="space-y-1.5">
              {steps.map((s) => (
                <AgentStepCard key={s.key} step={s} />
              ))}
            </div>
          </div>

          {/* Answer */}
          {answer && (
            <div className="rounded-lg border border-border bg-card/40 p-4">
              <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                <BarChart3 className="h-3 w-3" />
                答案
              </div>
              <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-foreground">
                {answer}
              </div>
              {chartSpec ? (
                <div className="mt-3 rounded border border-dashed border-border p-3 text-[11px] text-muted-foreground">
                  📊 图表建议: {(chartSpec as any).type} · {(chartSpec as any).title}
                  <span className="ml-2 text-muted-foreground/60">（图表渲染将在 Phase 3 接入）</span>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
      <KnowledgeIngestionPanel open={kbOpen} onClose={() => setKbOpen(false)} />
    </div>
  );
};

export default AgentOrchestrator;
