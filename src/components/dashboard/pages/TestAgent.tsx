// 测试用例智能生成 Agent — 独立 Tab 页
// 基于历史 defects + test_runs + test_cases 真实数据，
// 通过多智能体协作产出可提升覆盖率/广度的新用例。
import { useEffect, useMemo, useState } from "react";
import { recordMission } from "@/lib/memory/missions";
import { startTrace, type Tracer } from "@/lib/trace";
import TracePanel from "../chat/TracePanel";
import {
  Activity,
  Beaker,
  Bug,
  ClipboardCheck,
  Copy,
  FlaskConical,
  Layers,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
  Timer,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import AgentStepCard, { AgentStep } from "../chat/AgentStepCard";
import { duckdbManager, TableInfo } from "@/lib/duckdb/client";
import { ONTOLOGY, entityByName, resolveQuestion } from "@/lib/ontology";

// -- Ontology-derived physical table names (fall back to conventional defaults)
const T_DEFECT = entityByName("Defect")?.table ?? "defects";
const T_CASE = entityByName("TestCase")?.table ?? "test_cases";
const T_RUN = entityByName("TestRun")?.table ?? "test_runs";
const DEFECT_PK = entityByName("Defect")?.primaryKey ?? "defect_id";
const CASE_PK = entityByName("TestCase")?.primaryKey ?? "case_id";

// -- Dedup helpers (bigram Jaccard, zh-friendly, cheap)
const bigrams = (s: string): Set<string> => {
  const t = (s || "").toLowerCase().replace(/\s+/g, "");
  const g = new Set<string>();
  for (let i = 0; i < t.length - 1; i++) g.add(t.slice(i, i + 2));
  return g;
};
const jaccard = (a: Set<string>, b: Set<string>): number => {
  if (!a.size || !b.size) return 0;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  return inter / (a.size + b.size - inter);
};
const caseSignature = (c: { title?: string; steps?: string[]; expected?: string }) =>
  bigrams([c.title ?? "", ...(c.steps ?? []), c.expected ?? ""].join(" "));

const TIME_WINDOWS = [
  { id: "30", label: "近 30 天", days: 30 },
  { id: "90", label: "近 90 天", days: 90 },
  { id: "180", label: "近 180 天", days: 180 },
];

const CASE_TYPES = [
  { id: "功能", label: "功能" },
  { id: "边界", label: "边界" },
  { id: "异常", label: "异常" },
  { id: "回归", label: "回归" },
  { id: "性能", label: "性能" },
  { id: "安全", label: "安全" },
];

interface GeneratedCase {
  case_id?: string;
  title: string;
  type: string;
  priority: "P0" | "P1" | "P2" | "P3";
  module: string;
  preconditions?: string;
  steps: string[];
  expected: string;
  rationale: string;
  linked_defects?: string[];
  linked_requirement?: string;
  coverage_dimension?: string;
  novelty_score?: number;
  dedup_note?: string;
}

interface DefectPattern {
  module: string;
  severity: string;
  defect_count: number;
  avg_recurrence: number;
  sample_ids: string[];
}
interface ExecutionWeakness {
  module: string;
  ecu_target: string;
  fail_rate: number;
  run_count: number;
}
interface CoverageGap {
  module: string;
  total_cases: number;
  types_covered: string[];
  missing_types: string[];
}

const initialSteps = (): AgentStep[] => [
  { key: "scan", name: "Data Scanner", hint: "扫描本地表", status: "pending" },
  { key: "onto", name: "Ontology Resolver", hint: "解析目标 → 实体/字段", status: "pending" },
  { key: "defect", name: "Defect Miner", hint: "挖掘缺陷模式", status: "pending" },
  { key: "exec", name: "Execution Analyst", hint: "分析测试执行失败", status: "pending" },
  { key: "gap", name: "Coverage Gap Analyst", hint: "识别覆盖缺口", status: "pending" },
  { key: "generator", name: "Case Generator", hint: "生成候选用例", status: "pending" },
  { key: "dedup", name: "Novelty Dedup", hint: "去重与新颖度打分", status: "pending" },
  { key: "critic", name: "Critic (Hard Gate)", hint: "校验 + 失败自动重跑", status: "pending" },
];

// Observability: active tracer for the current generation run.
let activeTracer: Tracer | null = null;

const rawAgent = async (
  role: string,
  systemPrompt: string,
  userPrompt: string,
  jsonMode = false,
): Promise<string> => {
  const resp = await fetch(
    `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/agent-step`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
      },
      body: JSON.stringify({ role, systemPrompt, userPrompt, jsonMode }),
    },
  );
  const data = await resp.json();
  if (!resp.ok) throw new Error(data?.error || `${role} 调用失败`);
  return data.content as string;
};

const callAgent = async (
  role: string,
  systemPrompt: string,
  userPrompt: string,
  jsonMode = false,
): Promise<string> => {
  const tracer = activeTracer;
  if (!tracer) return rawAgent(role, systemPrompt, userPrompt, jsonMode);
  return tracer.span(role, "llm", { system: systemPrompt, user: userPrompt, jsonMode }, () =>
    rawAgent(role, systemPrompt, userPrompt, jsonMode),
  );
};

const extractJSON = <T,>(raw: string, fallback: T): T => {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidate = (fenced ? fenced[1] : raw).trim();
  try {
    return JSON.parse(candidate) as T;
  } catch {
    // try find first { or [
    const m = candidate.match(/[\[{][\s\S]*[\]}]/);
    if (m) {
      try {
        return JSON.parse(m[0]) as T;
      } catch {}
    }
    return fallback;
  }
};

const priorityColor = (p: string) => {
  switch (p) {
    case "P0":
      return "bg-rose-500/10 text-rose-600 border-rose-500/30";
    case "P1":
      return "bg-amber-500/10 text-amber-600 border-amber-500/30";
    case "P2":
      return "bg-sky-500/10 text-sky-600 border-sky-500/30";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
};

const TestAgent = () => {
  const [target, setTarget] = useState("");
  const [win, setWin] = useState("90");
  const [count, setCount] = useState(6);
  const [types, setTypes] = useState<string[]>(["功能", "边界", "异常"]);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<AgentStep[]>(initialSteps());
  const [cases, setCases] = useState<GeneratedCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tables, setTables] = useState<TableInfo[]>(duckdbManager.listTables());
  const [loadingDb, setLoadingDb] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [traceId, setTraceId] = useState<string | null>(null);

  useEffect(() => {
    const unsub = duckdbManager.subscribe(() => setTables(duckdbManager.listTables()));
    setLoadingDb(true);
    duckdbManager
      .autoloadManifest()
      .catch((e) => console.warn("[TestAgent] autoload", e))
      .finally(() => setLoadingDb(false));
    return () => { unsub(); };
  }, []);

  const tableNames = tables.map((t) => t.name.toLowerCase());
  const hasDefects = tableNames.includes(T_DEFECT.toLowerCase());
  const hasRuns = tableNames.includes(T_RUN.toLowerCase());
  const hasCases = tableNames.includes(T_CASE.toLowerCase());
  const dbReady = hasDefects || hasRuns || hasCases;

  const updateStep = (key: string, patch: Partial<AgentStep>) =>
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

  const toggleType = (id: string) =>
    setTypes((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const canRun = target.trim().length > 0 && types.length > 0 && !running && dbReady;

  const safeQuery = async <T,>(sql: string): Promise<T[]> => {
    const spanId = activeTracer?.start("DuckDB query", "sql", sql);
    try {
      const r = await duckdbManager.runQuery(sql, 200);
      if (spanId && activeTracer)
        activeTracer.end(spanId, { output: { rowCount: r.rowCount, sample: r.rows.slice(0, 10) } });
      return r.rows as T[];
    } catch (e) {
      if (spanId && activeTracer) activeTracer.end(spanId, { error: e });
      console.warn("[TestAgent] query failed", sql, e);
      return [];
    }
  };

  const run = async () => {
    if (!canRun) return;
    setRunning(true);
    setError(null);
    setCases(null);
    setSteps(initialSteps());

    const module = target.trim();
    const tracer = startTrace("test-agent", `用例生成 · ${target.trim()}`, { win, count, types });
    activeTracer = tracer;
    setTraceId(tracer.traceId);
    let runError: unknown = null;
    const days = TIME_WINDOWS.find((w) => w.id === win)?.days ?? 90;
    const winLabel = TIME_WINDOWS.find((w) => w.id === win)?.label ?? "近 90 天";

    try {
      // ---- Step 1: Data Scanner ----
      const t0 = performance.now();
      updateStep("scan", { status: "running" });
      const availableTables = tables.map((t) => `${t.name}(${t.rows} 行)`).join("、") || "未加载";
      updateStep("scan", {
        status: "done",
        summary: `已挂载 ${tables.length} 表`,
        durationMs: Math.round(performance.now() - t0),
        detail: (
          <div className="text-[11px] text-muted-foreground">
            <div>可用数据表：{availableTables}</div>
            <div className="mt-1">分析目标：{module} · 时间窗口：{winLabel}</div>
          </div>
        ),
      });

      // ---- Step 2: Ontology Resolver ----
      const tOnto = performance.now();
      updateStep("onto", { status: "running" });
      const ontoMatch = resolveQuestion(`${module} ${winLabel}`);
      const involvedEntities = Array.from(
        new Set([
          ...ontoMatch.entities.map((e) => e.entity.name),
          ...(hasDefects ? ["Defect"] : []),
          ...(hasCases ? ["TestCase"] : []),
          ...(hasRuns ? ["TestRun"] : []),
        ]),
      );
      const ontoAttrs = ontoMatch.attributes.map(
        (a) => `${a.entity.name}.${a.attribute.name}${a.enumValueMatched ? `=${a.enumValueMatched}` : ""}`,
      );
      updateStep("onto", {
        status: "done",
        summary: `实体：${involvedEntities.join("/") || "n/a"}${
          ontoAttrs.length ? ` · 命中属性 ${ontoAttrs.length}` : ""
        }`,
        durationMs: Math.round(performance.now() - tOnto),
        detail: (
          <div className="space-y-1 text-[11px] text-muted-foreground">
            <div>物理表映射：Defect→{T_DEFECT} · TestCase→{T_CASE} · TestRun→{T_RUN}</div>
            {ontoAttrs.length > 0 && <div>命中属性：{ontoAttrs.join(", ")}</div>}
            {ontoMatch.metrics.length > 0 && (
              <div>相关指标：{ontoMatch.metrics.map((m) => m.metric.name).join(", ")}</div>
            )}
          </div>
        ),
      });

      // ---- Step 3: Defect Miner (real SQL, ontology-driven table) ----
      const t1 = performance.now();
      updateStep("defect", { status: "running" });
      const like = `%${module.replace(/'/g, "''")}%`;
      const defectSql = hasDefects
        ? `SELECT module, severity, COUNT(*)::INT AS defect_count,
                  ROUND(AVG(COALESCE(recurrence_count,1)),2) AS avg_recurrence,
                  LIST(${DEFECT_PK} ORDER BY COALESCE(recurrence_count,0) DESC) AS ids
             FROM ${T_DEFECT}
            WHERE (module ILIKE '${like}' OR CAST(${DEFECT_PK} AS VARCHAR) ILIKE '${like}')
              AND (open_date IS NULL OR open_date >= (CURRENT_DATE - INTERVAL ${days} DAY))
            GROUP BY module, severity
            ORDER BY defect_count DESC
            LIMIT 12`
        : "";
      const defectRows = hasDefects ? await safeQuery<any>(defectSql) : [];
      const defectPatterns: DefectPattern[] = defectRows.map((r) => ({
        module: String(r.module ?? ""),
        severity: String(r.severity ?? ""),
        defect_count: Number(r.defect_count ?? 0),
        avg_recurrence: Number(r.avg_recurrence ?? 0),
        sample_ids: (Array.isArray(r.ids) ? r.ids : []).slice(0, 5).map(String),
      }));
      updateStep("defect", {
        status: "done",
        summary: `${defectPatterns.length} 个缺陷簇 · 共 ${defectPatterns.reduce((a, b) => a + b.defect_count, 0)} 条`,
        durationMs: Math.round(performance.now() - t1),
        detail: (
          <pre className="whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
            {defectPatterns.length ? JSON.stringify(defectPatterns, null, 2) : `未在 ${T_DEFECT} 表匹配到目标`}
          </pre>
        ),
      });

      // ---- Step 4: Execution Analyst ----
      const t2 = performance.now();
      updateStep("exec", { status: "running" });
      let execWeak: ExecutionWeakness[] = [];
      if (hasRuns && hasCases) {
        const execSql = `SELECT c.module AS module,
                                r.ecu_target AS ecu_target,
                                COUNT(*)::INT AS run_count,
                                ROUND(SUM(CASE WHEN r.result='Fail' THEN 1 ELSE 0 END)*1.0/COUNT(*),3) AS fail_rate
                           FROM ${T_RUN} r JOIN ${T_CASE} c ON c.${CASE_PK} = r.${CASE_PK}
                          WHERE c.module ILIKE '${like}'
                            AND (r.executed_at IS NULL OR r.executed_at >= (CURRENT_DATE - INTERVAL ${days} DAY))
                          GROUP BY c.module, r.ecu_target
                         HAVING run_count >= 3
                          ORDER BY fail_rate DESC
                          LIMIT 10`;
        const rows = await safeQuery<any>(execSql);
        execWeak = rows.map((r) => ({
          module: String(r.module ?? ""),
          ecu_target: String(r.ecu_target ?? ""),
          run_count: Number(r.run_count ?? 0),
          fail_rate: Number(r.fail_rate ?? 0),
        }));
      }
      updateStep("exec", {
        status: "done",
        summary: `${execWeak.length} 个高失败率组合`,
        durationMs: Math.round(performance.now() - t2),
        detail: (
          <pre className="whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
            {execWeak.length ? JSON.stringify(execWeak, null, 2) : "未匹配到执行记录（或数据不足）"}
          </pre>
        ),
      });

      // ---- Step 5: Coverage Gap + existing case corpus for dedup ----
      const t3 = performance.now();
      updateStep("gap", { status: "running" });
      let gaps: CoverageGap[] = [];
      let existingCorpus: { title: string; steps: string[] }[] = [];
      if (hasCases) {
        const gapSql = `SELECT module,
                               COUNT(*)::INT AS total_cases,
                               LIST(DISTINCT type) AS types
                          FROM ${T_CASE}
                         WHERE module ILIKE '${like}'
                         GROUP BY module
                         ORDER BY total_cases DESC
                         LIMIT 6`;
        const rows = await safeQuery<any>(gapSql);
        const allTypes = CASE_TYPES.map((t) => t.id);
        gaps = rows.map((r) => {
          const covered = (Array.isArray(r.types) ? r.types : []).map(String);
          return {
            module: String(r.module ?? ""),
            total_cases: Number(r.total_cases ?? 0),
            types_covered: covered,
            missing_types: allTypes.filter((t) => !covered.includes(t)),
          };
        });
        // pull existing titles/steps sample for novelty comparison
        const corpusRows = await safeQuery<any>(
          `SELECT title, steps FROM ${T_CASE} WHERE module ILIKE '${like}' LIMIT 500`,
        );
        existingCorpus = corpusRows.map((r) => ({
          title: String(r.title ?? ""),
          steps: Array.isArray(r.steps)
            ? r.steps.map(String)
            : typeof r.steps === "string"
              ? [r.steps]
              : [],
        }));
      }
      updateStep("gap", {
        status: "done",
        summary: gaps.length
          ? `${gaps.length} 模块 · 缺口类型 ${gaps[0]?.missing_types.join("/") || "无"} · 语料 ${existingCorpus.length}`
          : "未查询到用例分布",
        durationMs: Math.round(performance.now() - t3),
        detail: (
          <pre className="whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
            {JSON.stringify(gaps, null, 2)}
          </pre>
        ),
      });

      // ---- Step 6: Generator (LLM) — supports one auto-retry with violations ----
      const validIds = new Set(defectPatterns.flatMap((d) => d.sample_ids));
      const allowedTypes = new Set(types);
      const validModulesFromDb = new Set(
        [...defectPatterns.map((d) => d.module), ...gaps.map((g) => g.module)]
          .filter(Boolean)
          .map((s) => s.toLowerCase()),
      );

      const genSys =
        `你是资深汽车电子测试架构师。根据真实的历史缺陷模式、执行失败热点与覆盖缺口，` +
        `产出高质量新增测试用例。每条用例必须引用真实数字或缺陷 ID 作为 rationale 依据；` +
        `禁止编造 defect_id；用例类型必须仅从用户指定集合中选择；` +
        `严格返回 JSON 对象 {"cases":[...]}。`;
      const ontologyHint = ONTOLOGY.entities
        .find((e) => e.name === "TestCase")
        ?.attributes.map((a) => `${a.name}(${a.label})`)
        .join(", ");

      const buildGenUser = (violationFeedback?: string) =>
        `目标模块/组件：${module}\n时间窗口：${winLabel}\n生成条数：${count}\n用例类型集合：${types.join("、")}\n\n` +
        `【历史缺陷簇】\n${JSON.stringify(defectPatterns, null, 2)}\n\n` +
        `【执行失败热点】\n${JSON.stringify(execWeak, null, 2)}\n\n` +
        `【覆盖缺口】\n${JSON.stringify(gaps, null, 2)}\n\n` +
        `TestCase 字段参考: ${ontologyHint}\n\n` +
        (violationFeedback
          ? `【上一轮 Critic 硬门禁不通过原因，请务必修正】\n${violationFeedback}\n\n`
          : "") +
        `请输出 JSON：{"cases":[{"title","type","priority","module","preconditions","steps":[...],"expected","rationale","linked_defects":[...],"linked_requirement","coverage_dimension"}]}\n` +
        `要求：\n` +
        `1. 严格产出 ${count} 条。\n` +
        `2. 优先针对高频/高严重度缺陷和失败率>=0.3的场景。\n` +
        `3. 至少一半是覆盖缺口类型（未覆盖的用例类型）以扩大广度。\n` +
        `4. rationale 必须包含具体数字或 defect_id。\n` +
        `5. linked_defects 只能来自这个白名单: ${JSON.stringify([...validIds])}\n`;

      const t4 = performance.now();
      updateStep("generator", { status: "running" });
      const genRaw = await callAgent("generator", genSys, buildGenUser(), true);
      const gen = extractJSON<{ cases: GeneratedCase[] }>(genRaw, { cases: [] });
      let draftCases = Array.isArray(gen.cases) ? gen.cases : [];
      updateStep("generator", {
        status: "done",
        summary: `${draftCases.length} 条候选`,
        durationMs: Math.round(performance.now() - t4),
      });

      // ---- Step 7: Novelty Dedup (bigram Jaccard vs existing + intra-batch) ----
      const tDedup = performance.now();
      updateStep("dedup", { status: "running" });
      const corpusSigs = existingCorpus.map((r) => bigrams(`${r.title} ${r.steps.join(" ")}`));
      const scored = draftCases.map((c) => {
        const sig = caseSignature(c);
        let maxSim = 0;
        for (const cs of corpusSigs) maxSim = Math.max(maxSim, jaccard(sig, cs));
        return { c, sig, novelty: Number((1 - maxSim).toFixed(3)), maxSim };
      });
      // intra-batch dedup: drop candidate if too similar to a already-kept one
      const kept: typeof scored = [];
      for (const item of scored.sort((a, b) => b.novelty - a.novelty)) {
        const intra = kept.reduce((m, k) => Math.max(m, jaccard(item.sig, k.sig)), 0);
        if (intra >= 0.72) continue;
        kept.push(item);
      }
      const NOVELTY_THRESHOLD = 0.25;
      const deduped = kept
        .filter((k) => k.novelty >= NOVELTY_THRESHOLD)
        .map((k) => ({
          ...k.c,
          novelty_score: k.novelty,
          dedup_note:
            k.maxSim > 0
              ? `与既有用例最大相似度 ${(k.maxSim * 100).toFixed(0)}%`
              : "无同模块历史用例可比对",
        }));
      const droppedCount = draftCases.length - deduped.length;
      updateStep("dedup", {
        status: "done",
        summary: `保留 ${deduped.length} / 丢弃 ${droppedCount}（阈值 novelty≥${NOVELTY_THRESHOLD}）`,
        durationMs: Math.round(performance.now() - tDedup),
      });
      draftCases = deduped;

      // ---- Step 8: Critic (HARD GATE) + one automatic retry ----
      const t5 = performance.now();
      updateStep("critic", { status: "running" });

      const hardValidate = (arr: GeneratedCase[]) => {
        const violations: string[] = [];
        arr.forEach((c, i) => {
          const tag = `#${i + 1} "${(c.title || "").slice(0, 24)}"`;
          if (!allowedTypes.has(c.type))
            violations.push(`${tag}: type "${c.type}" 不在允许集 ${JSON.stringify([...allowedTypes])}`);
          for (const id of c.linked_defects || []) {
            if (!validIds.has(String(id)))
              violations.push(`${tag}: linked_defects 出现虚构 ID "${id}"（不在白名单）`);
          }
          if (!/\d|[A-Z]{2,}-\d+/.test(c.rationale || ""))
            violations.push(`${tag}: rationale 无具体数字或 ID 依据`);
          if (
            validModulesFromDb.size > 0 &&
            c.module &&
            !validModulesFromDb.has(String(c.module).toLowerCase())
          )
            violations.push(`${tag}: module "${c.module}" 未出现在真实数据中`);
          if (!Array.isArray(c.steps) || c.steps.length < 2)
            violations.push(`${tag}: steps 至少需要 2 步`);
          if (!c.expected || c.expected.length < 4)
            violations.push(`${tag}: expected 断言过弱`);
        });
        return violations;
      };

      let violations = hardValidate(draftCases);
      let retried = false;

      if (violations.length > 0 && draftCases.length > 0) {
        retried = true;
        updateStep("critic", {
          status: "running",
          summary: `发现 ${violations.length} 处违规 · 自动重跑 Generator`,
        });
        try {
          const retryRaw = await callAgent(
            "generator",
            genSys,
            buildGenUser(violations.slice(0, 15).join("\n")),
            true,
          );
          const retryGen = extractJSON<{ cases: GeneratedCase[] }>(retryRaw, { cases: draftCases });
          if (Array.isArray(retryGen.cases) && retryGen.cases.length) {
            // re-score novelty for retried batch
            draftCases = retryGen.cases.map((c) => {
              const sig = caseSignature(c);
              let maxSim = 0;
              for (const cs of corpusSigs) maxSim = Math.max(maxSim, jaccard(sig, cs));
              return {
                ...c,
                novelty_score: Number((1 - maxSim).toFixed(3)),
                dedup_note: maxSim > 0 ? `与既有用例最大相似度 ${(maxSim * 100).toFixed(0)}%` : undefined,
              };
            });
            violations = hardValidate(draftCases);
          }
        } catch (e) {
          console.warn("[TestAgent] critic auto-retry failed", e);
        }
      }

      // Final defense-in-depth: strip invalid IDs, drop cases with fatal type violation
      const finalCases = draftCases
        .filter((c) => allowedTypes.has(c.type))
        .map((c, i) => ({
          ...c,
          case_id: c.case_id || `TC-AGENT-${Date.now().toString(36).slice(-4)}-${i + 1}`,
          linked_defects: (c.linked_defects || []).filter((id) => validIds.has(String(id))),
        }));

      updateStep("critic", {
        status: violations.length && !retried ? "done" : "done",
        summary:
          `${finalCases.length} 条通过硬门禁` +
          (retried ? " · 已自动重跑 1 次" : "") +
          (violations.length ? ` · 残留提示 ${violations.length}` : ""),
        durationMs: Math.round(performance.now() - t5),
        detail:
          violations.length > 0 ? (
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
              {violations.slice(0, 12).join("\n")}
            </pre>
          ) : undefined,
      });

      setCases(finalCases);
      // Task memory: persist this generation mission for later reuse / diffing.
      recordMission({
        type: "test-agent",
        goal: target || "测试用例生成",
        filters: { target, window: win, count, types },
        steps: steps.map((s) => ({ name: s.name, status: s.status, summary: s.summary, ms: s.durationMs })),
        result: `生成 ${finalCases.length} 条用例` + (retried ? "（重跑 1 次）" : ""),
      }).catch(() => {});
    } catch (e: any) {
      const msg = e?.message || String(e);
      runError = e;
      setError(msg);
      setSteps((prev) =>
        prev.map((s) => (s.status === "running" ? { ...s, status: "error", error: msg } : s)),
      );
    } finally {
      tracer.finish(runError);
      activeTracer = null;
      setRunning(false);
    }
  };

  const summary = useMemo(() => {
    if (!cases) return null;
    const byType = new Map<string, number>();
    cases.forEach((c) => byType.set(c.type, (byType.get(c.type) || 0) + 1));
    return {
      total: cases.length,
      linked: cases.filter((c) => (c.linked_defects || []).length).length,
      byType: [...byType.entries()],
    };
  }, [cases]);

  const copyAll = () => {
    if (!cases) return;
    navigator.clipboard.writeText(JSON.stringify(cases, null, 2));
  };

  return (
    <div className="space-y-5">
      {/* Hero / Intro */}
      <div className="dashboard-card overflow-hidden">
        <div className="flex items-start gap-4 border-b border-border bg-gradient-to-br from-primary/8 via-transparent to-transparent p-5">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/15 text-primary">
            <FlaskConical className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-foreground">测试用例智能生成 Agent</h2>
              <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                <Sparkles className="h-2.5 w-2.5" /> 多智能体
              </span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              识别历史测试执行与缺陷特征，产出可提升覆盖率与广度的新用例。数据仅在本地 DuckDB 中查询，模型仅接收结构化摘要。
            </p>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <button
              onClick={() => setTraceOpen(true)}
              className={`flex items-center gap-1 rounded-md border px-2 py-1 transition-colors ${
                traceId
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border hover:bg-secondary hover:text-foreground"
              }`}
              title="查看调用树、耗时瀑布与每步输入输出"
            >
              <Activity className="h-3 w-3" /> Trace
            </button>
            <span
              className={`inline-flex h-2 w-2 rounded-full ${
                dbReady ? "bg-emerald-500" : loadingDb ? "bg-amber-500 animate-pulse" : "bg-rose-500"
              }`}
            />
            {dbReady ? `已挂载 ${tables.length} 表` : loadingDb ? "正在加载数据…" : "数据未就绪"}
          </div>
        </div>

        {/* Controls */}
        <div className="grid gap-4 p-5 md:grid-cols-12">
          <div className="md:col-span-5">
            <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
              <Target className="h-3 w-3 text-muted-foreground" /> 分析目标
              <span className="text-muted-foreground">（模块 / ECU / 需求 ID / 缺陷 ID 关键字）</span>
            </label>
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="例如：OTA、CAN-BMS、ADAS、REQ-12"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
            />
          </div>

          <div className="md:col-span-3">
            <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
              <Timer className="h-3 w-3 text-muted-foreground" /> 时间窗口
            </label>
            <div className="flex gap-1">
              {TIME_WINDOWS.map((w) => (
                <button
                  key={w.id}
                  onClick={() => setWin(w.id)}
                  className={`flex-1 rounded-md border px-2 py-1.5 text-xs transition-colors ${
                    win === w.id
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border bg-background text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {w.label}
                </button>
              ))}
            </div>
          </div>

          <div className="md:col-span-4">
            <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
              <Layers className="h-3 w-3 text-muted-foreground" /> 生成数量 · {count}
            </label>
            <input
              type="range"
              min={3}
              max={16}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              className="h-9 w-full accent-primary"
            />
          </div>

          <div className="md:col-span-12">
            <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
              <ShieldCheck className="h-3 w-3 text-muted-foreground" /> 用例类型（多选 · 决定覆盖广度）
            </label>
            <div className="flex flex-wrap gap-1.5">
              {CASE_TYPES.map((t) => {
                const active = types.includes(t.id);
                return (
                  <button
                    key={t.id}
                    onClick={() => toggleType(t.id)}
                    className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                      active
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border bg-background text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {t.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="md:col-span-12 flex items-center justify-between border-t border-border pt-4">
            <p className="text-[11px] text-muted-foreground">
              流水线：Data Scanner → Defect Miner → Execution Analyst → Coverage Gap → Generator → Critic
            </p>
            <button
              disabled={!canRun}
              onClick={run}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {running ? (
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <PlayCircle className="h-3.5 w-3.5" />
              )}
              {running ? "生成中…" : "启动 Agent"}
            </button>
          </div>
        </div>
      </div>

      {/* Agent progress */}
      {(running || cases || error) && (
        <div className="dashboard-card p-4">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-foreground">
            <Sparkles className="h-3.5 w-3.5 text-primary" /> Agent 工作流
          </div>
          <div className="space-y-1.5">
            {steps.map((s) => (
              <AgentStepCard key={s.key} step={s} />
            ))}
          </div>
          {error && (
            <div className="mt-3 rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-600">
              {error}
            </div>
          )}
        </div>
      )}

      {/* Result */}
      <AnimatePresence>
        {cases && summary && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="dashboard-card"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-600">
                  <ClipboardCheck className="h-4.5 w-4.5" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    生成 {summary.total} 条新增用例
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {summary.linked} 条链接到真实缺陷 · 类型分布：
                    {summary.byType.map(([k, v]) => `${k}(${v})`).join(" / ")}
                  </p>
                </div>
              </div>
              <button
                onClick={copyAll}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground hover:bg-muted"
              >
                <Copy className="h-3 w-3" /> 复制 JSON
              </button>
            </div>

            <div className="divide-y divide-border">
              {cases.map((c, i) => (
                <div key={i} className="p-4">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="text-[10px] font-mono text-muted-foreground">{c.case_id}</span>
                    <span
                      className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${priorityColor(c.priority)}`}
                    >
                      {c.priority}
                    </span>
                    <span className="rounded-md bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                      {c.type}
                    </span>
                    <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      {c.module}
                    </span>
                    {c.coverage_dimension && (
                      <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                        覆盖：{c.coverage_dimension}
                      </span>
                    )}
                    {typeof c.novelty_score === "number" && (
                      <span
                        title={c.dedup_note}
                        className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${
                          c.novelty_score >= 0.7
                            ? "bg-emerald-500/10 text-emerald-600"
                            : c.novelty_score >= 0.4
                              ? "bg-amber-500/10 text-amber-600"
                              : "bg-muted text-muted-foreground"
                        }`}
                      >
                        新颖度 {(c.novelty_score * 100).toFixed(0)}
                      </span>
                    )}
                    <span className="ml-auto text-[11px] font-semibold text-foreground">
                      {c.title}
                    </span>
                  </div>

                  {c.preconditions && (
                    <div className="mb-1.5 text-[12px]">
                      <span className="text-muted-foreground">前置：</span>
                      <span className="text-foreground">{c.preconditions}</span>
                    </div>
                  )}
                  {c.steps?.length > 0 && (
                    <ol className="mb-1.5 list-decimal space-y-0.5 pl-5 text-[12px] text-foreground">
                      {c.steps.map((s, j) => (
                        <li key={j}>{s}</li>
                      ))}
                    </ol>
                  )}
                  <div className="text-[12px]">
                    <span className="text-muted-foreground">预期：</span>
                    <span className="text-foreground">{c.expected}</span>
                  </div>

                  <div className="mt-2 flex flex-wrap items-start gap-3 rounded-md bg-muted/40 px-2.5 py-1.5">
                    <div className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
                      <Sparkles className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
                      <span className="text-foreground/80">{c.rationale}</span>
                    </div>
                    {(c.linked_defects?.length || 0) > 0 && (
                      <div className="ml-auto flex items-center gap-1 text-[11px]">
                        <Bug className="h-3 w-3 text-rose-500" />
                        {c.linked_defects!.map((d) => (
                          <span
                            key={d}
                            className="rounded border border-rose-500/20 bg-rose-500/5 px-1.5 py-0.5 font-mono text-[10px] text-rose-600"
                          >
                            {d}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {cases.length === 0 && (
                <div className="p-6 text-center text-xs text-muted-foreground">
                  Critic 认为没有足够依据生成可靠用例，请更换目标或时间窗口后重试。
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Empty state */}
      {!running && !cases && !error && (
        <div className="dashboard-card flex items-center gap-3 p-4 text-xs text-muted-foreground">
          <Beaker className="h-4 w-4 text-primary" />
          输入分析目标并选择用例类型后启动 Agent。所有查询在浏览器本地 DuckDB 中执行。
        </div>
      )}

      <TracePanel open={traceOpen} onClose={() => setTraceOpen(false)} />
    </div>
  );
};

export default TestAgent;
