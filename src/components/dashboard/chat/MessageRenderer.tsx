import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { motion, AnimatePresence } from "framer-motion";
import {
  Brain,
  ChevronDown,
  Check,
  Copy,
  Database,
  Loader2,
  BarChart3,
  ListChecks,
  Wrench,
  AlertTriangle,
  Table as TableIcon,
  Play,
  Terminal,
  PanelRightOpen,
  ClipboardList,
  Download,
  Bug,
  FileText,
  FileSpreadsheet,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import * as XLSX from "xlsx";
import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AgentSegment, parseAgentStream, type TestCaseItem } from "./agentParser";
import type { Artifact } from "./ArtifactCanvas";

export interface ToolResult {
  ok: boolean;
  ms?: number;
  data?: any;
  error?: string;
}

interface Props {
  content: string;
  streaming: boolean;
  onPickFollowup?: (q: string) => void;
  toolResults?: Record<string, ToolResult>;
  onOpenCanvas?: (a: Artifact) => void;
}

// Replaces <cite source="x">label</cite> in markdown with a custom token,
// then renders a clickable chip via remark-react custom components.
// Simpler: pre-process text to ` [label](#cite:source) ` so react-markdown renders a link, we restyle in components.
function preprocessCitations(md: string) {
  return md.replace(
    /<cite\s+source="([^"]+)">([^<]+)<\/cite>/gi,
    (_m, src, label) => `[${label}](#cite:${encodeURIComponent(src)})`,
  );
}

export default function MessageRenderer({ content, streaming, onPickFollowup, toolResults, onOpenCanvas }: Props) {
  const segs = parseAgentStream(content);
  const completedSteps = segs.filter((s) => s.kind === "step" && s.closed).length;
  return (
    <div className="space-y-3">
      {segs.map((s, i) => {
        if (s.kind === "think")
          return <ThinkBlock key={i} text={s.text} closed={s.closed} streaming={streaming} />;
        if (s.kind === "plan")
          return (
            <PlanBlock
              key={i}
              items={s.items}
              completed={completedSteps}
              closed={s.closed}
              streaming={streaming}
            />
          );
        if (s.kind === "step")
          return (
            <StepBlock
              key={i}
              title={s.title}
              source={s.source}
              text={s.text}
              closed={s.closed}
            />
          );
        if (s.kind === "chart")
          return (
            <ChartBlock
              key={i}
              chartType={s.chartType}
              title={s.title}
              text={s.text}
              closed={s.closed}
              onOpenCanvas={onOpenCanvas}
            />
          );
        if (s.kind === "tool")
          return (
            <ToolBlock
              key={i}
              toolName={s.toolName}
              toolId={s.toolId}
              args={s.args}
              text={s.text}
              closed={s.closed}
              result={toolResults?.[s.toolId]}
              onOpenCanvas={onOpenCanvas}
            />
          );
        if (s.kind === "followup")
          return (
            <FollowupBlock
              key={i}
              items={s.items}
              closed={s.closed}
              streaming={streaming}
              onPick={onPickFollowup}
            />
          );
        if (s.kind === "testcases")
          return (
            <TestCasesBlock
              key={i}
              module={s.module}
              title={s.title}
              items={s.items}
              raw={s.raw}
              closed={s.closed}
            />
          );
        return <TextBlock key={i} text={s.text} />;
      })}
    </div>
  );
}

function ToolBlock({
  toolName,
  toolId,
  args,
  text,
  closed,
  result,
  onOpenCanvas,
}: {
  toolName: string;
  toolId: string;
  args?: Record<string, string>;
  text: string;
  closed: boolean;
  result?: ToolResult;
  onOpenCanvas?: (a: import("./ArtifactCanvas").Artifact) => void;
}) {
  const [open, setOpen] = useState(false);
  const labelMap: Record<string, string> = {
    query_sql: "查询 SQL",
    profile_table: "数据画像",
    list_tables: "列出表",
    risk_scan: "风险扫描",
    run_python: "Python 代码",
    forecast: "时序预测",
    detect_anomaly: "异常检测",
    web_search: "联网搜索",
  };
  const label = labelMap[toolName] || toolName;

  const status = !closed
    ? "writing"
    : !result
      ? "running"
      : result.ok
        ? "ok"
        : "error";
  const target = args?.table || args?.name;

  const canCanvas =
    onOpenCanvas && status === "ok" &&
    ((toolName === "query_sql" && Array.isArray(result?.data?.rows)) ||
     (toolName === "run_python"));
  const openCanvas = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onOpenCanvas || !result?.ok) return;
    if (toolName === "query_sql") {
      onOpenCanvas({
        kind: "sql",
        title: args?.table ? `查询 · ${args.table}` : "SQL 查询",
        sql: text.trim() || "",
        columns: result.data.columns || [],
        rows: result.data.rows || [],
        rowCount: result.data.rowCount ?? result.data.rows?.length ?? 0,
      });
    } else if (toolName === "run_python") {
      onOpenCanvas({
        kind: "python",
        title: "Python 执行",
        stdout: result.data?.stdout,
        value: result.data?.value,
        figures: result.data?.figures,
      });
    }
  };

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <div
          className={`flex h-5 w-5 items-center justify-center rounded-md ${
            status === "ok"
              ? "bg-emerald-500/10 text-emerald-600"
              : status === "error"
                ? "bg-destructive/10 text-destructive"
                : "bg-primary/10 text-primary"
          }`}
        >
          {status === "writing" || status === "running" ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : status === "error" ? (
            <AlertTriangle className="h-3 w-3" />
          ) : toolName === "query_sql" ? (
            <Play className="h-3 w-3" />
          ) : toolName === "profile_table" ? (
            <TableIcon className="h-3 w-3" />
          ) : toolName === "run_python" ? (
            <Terminal className="h-3 w-3" />
          ) : (
            <Wrench className="h-3 w-3" />
          )}

        </div>
        <p className="text-xs font-semibold text-foreground">{label}</p>
        {target && (
          <span className="rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            {target}
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
          {status === "ok" && result?.ms != null && <span>{result.ms}ms</span>}
          {status === "ok" && result?.data?.rowCount != null && (
            <span>{result.data.rowCount} 行</span>
          )}
          {status === "ok" && toolName === "run_python" && Array.isArray(result?.data?.figures) && result.data.figures.length > 0 && (
            <span>{result.data.figures.length} 图</span>
          )}
          {status === "error" && <span className="text-destructive">失败</span>}
          {status === "writing" && <span>生成中</span>}
          {status === "running" && <span>执行中</span>}

          {canCanvas && (
            <span
              role="button"
              onClick={openCanvas}
              title="在画布中打开"
              className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-1.5 py-0.5 text-foreground hover:border-primary/40 hover:text-primary"
            >
              <PanelRightOpen className="h-3 w-3" /> 画布
            </span>
          )}
          <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-border/60"
          >
            {text.trim() && (
              <pre className="overflow-x-auto bg-muted/40 px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground">
                {text.trim()}
              </pre>
            )}
            {result?.ok && result.data && (
              <ToolResultPreview name={toolName} data={result.data} />
            )}
            {result?.error && (
              <p className="px-3 py-2 text-xs text-destructive">{result.error}</p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ToolResultPreview({ name, data }: { name: string; data: any }) {
  if (name === "run_python") {
    return (
      <div className="space-y-2 px-3 py-2">
        {data.stdout && (
          <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-background px-2 py-1.5 font-mono text-[11px] leading-relaxed text-foreground">
            {data.stdout.slice(0, 4000)}
          </pre>
        )}
        {data.value && (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">返回值</p>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-background px-2 py-1.5 font-mono text-[11px] leading-relaxed text-muted-foreground">
              {data.value}
            </pre>
          </div>
        )}
        {Array.isArray(data.figures) && data.figures.length > 0 && (
          <div className="grid gap-2">
            {data.figures.map((b64: string, i: number) => (
              <img
                key={i}
                src={`data:image/png;base64,${b64}`}
                alt={`figure ${i + 1}`}
                className="w-full rounded-md border border-border"
              />
            ))}
          </div>
        )}
        {data.error && (
          <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-destructive/10 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-destructive">
            {data.error}
          </pre>
        )}
        {Array.isArray(data.tablesLoaded) && data.tablesLoaded.length > 0 && (
          <p className="text-[10px] text-muted-foreground">
            注入 DataFrame: {data.tablesLoaded.join(", ")}
          </p>
        )}
      </div>
    );
  }
  if (name === "query_sql" && Array.isArray(data.rows)) {

    const rows = data.rows.slice(0, 8);
    const cols: string[] = data.columns || (rows[0] ? Object.keys(rows[0]) : []);
    if (!rows.length) return <p className="px-3 py-2 text-xs text-muted-foreground">空结果</p>;
    return (
      <div className="overflow-x-auto px-3 py-2">
        <table className="w-full border-collapse text-[11px]">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              {cols.map((c) => (
                <th key={c} className="py-1 pr-3 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              <tr key={i} className="border-b border-border/40">
                {cols.map((c) => (
                  <td key={c} className="py-1 pr-3 font-mono text-foreground">
                    {formatCell(r[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {data.rowCount > rows.length && (
          <p className="mt-1 text-[10px] text-muted-foreground">
            显示前 {rows.length} / {data.rowCount} 行
          </p>
        )}
      </div>
    );
  }
  return (
    <pre className="overflow-x-auto bg-background px-3 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
      {JSON.stringify(data, null, 2).slice(0, 1200)}
    </pre>
  );
}

function formatCell(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toString() : v.toFixed(3);
  const s = String(v);
  return s.length > 60 ? s.slice(0, 57) + "…" : s;
}

function FollowupBlock({
  items,
  closed,
  streaming,
  onPick,
}: {
  items: string[];
  closed: boolean;
  streaming: boolean;
  onPick?: (q: string) => void;
}) {
  // Only show once fully closed to avoid flickering during stream.
  if (!closed || !items.length) return null;
  return (
    <div className="mt-1">
      <p className="mb-1.5 inline-flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        <span className="h-1 w-1 rounded-full bg-primary" />
        追问建议
      </p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((q, i) => (
          <button
            key={i}
            onClick={() => onPick?.(q)}
            disabled={streaming || !onPick}
            className="group/fu inline-flex items-center gap-1 rounded-full border border-border bg-card px-2.5 py-1 text-[11px] text-foreground transition-all hover:border-primary/50 hover:bg-primary/5 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span>{q}</span>
            <span className="text-muted-foreground group-hover/fu:text-primary">→</span>
          </button>
        ))}
      </div>
    </div>
  );
}


function PlanBlock({
  items,
  completed,
  closed,
  streaming,
}: {
  items: string[];
  completed: number;
  closed: boolean;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(true);
  if (!items.length) return null;
  const total = items.length;
  const done = Math.min(completed, total);
  const inProgress = streaming && closed && done < total ? done : -1;
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 border-b border-border/60 px-3 py-2 text-left"
      >
        <div className="flex h-5 w-5 items-center justify-center rounded-md bg-primary/10 text-primary">
          <ListChecks className="h-3 w-3" />
        </div>
        <p className="text-xs font-semibold text-foreground">任务计划</p>
        <span className="ml-auto inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
          {done}/{total}
          <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.ol
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden px-3 py-2 text-xs"
          >
            {items.map((it, idx) => {
              const isDone = idx < done;
              const isActive = idx === inProgress;
              return (
                <li key={idx} className="flex items-start gap-2 py-1">
                  <span
                    className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                      isDone
                        ? "border-primary bg-primary text-primary-foreground"
                        : isActive
                        ? "border-primary/60 bg-primary/10 text-primary"
                        : "border-border bg-background text-muted-foreground"
                    }`}
                  >
                    {isDone ? (
                      <Check className="h-2.5 w-2.5" />
                    ) : isActive ? (
                      <Loader2 className="h-2.5 w-2.5 animate-spin" />
                    ) : (
                      <span className="text-[9px] font-medium">{idx + 1}</span>
                    )}
                  </span>
                  <span
                    className={
                      isDone
                        ? "text-muted-foreground line-through decoration-muted-foreground/40"
                        : isActive
                        ? "font-medium text-foreground"
                        : "text-foreground"
                    }
                  >
                    {it}
                  </span>
                </li>
              );
            })}
          </motion.ol>
        )}
      </AnimatePresence>
    </div>
  );
}

function ThinkBlock({
  text,
  closed,
  streaming,
}: {
  text: string;
  closed: boolean;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(false);
  const active = streaming && !closed;
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/40">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        {active ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Brain className="h-3 w-3" />
        )}
        <span>{active ? "思考中…" : "思考过程"}</span>
        <ChevronDown
          className={`ml-auto h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <p className="whitespace-pre-wrap px-3 pb-2.5 pt-0.5 text-xs leading-relaxed text-muted-foreground">
              {text.trim()}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function StepBlock({
  title,
  source,
  text,
  closed,
}: {
  title: string;
  source?: string;
  text: string;
  closed: boolean;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -4 }}
      animate={{ opacity: 1, x: 0 }}
      className="flex items-start gap-2.5 rounded-lg border border-border bg-card px-3 py-2"
    >
      <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
        {closed ? <Wrench className="h-3 w-3" /> : <Loader2 className="h-3 w-3 animate-spin" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <p className="text-xs font-semibold text-foreground">{title}</p>
          {source && (
            <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              <Database className="h-2.5 w-2.5" />
              {source}
            </span>
          )}
        </div>
        {text.trim() && (
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            {text.trim()}
          </p>
        )}
      </div>
    </motion.div>
  );
}

const CHART_PALETTE = [
  "hsl(var(--primary))",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
  "#ec4899",
  "#84cc16",
];

function ChartBlock({
  chartType,
  title,
  text,
  closed,
  onOpenCanvas,
}: {
  chartType: "line" | "bar" | "area" | "pie";
  title?: string;
  text: string;
  closed: boolean;
  onOpenCanvas?: (a: import("./ArtifactCanvas").Artifact) => void;
}) {
  const spec = useMemo(() => {
    if (!closed) return null;
    try {
      const cleaned = text.trim().replace(/^```json\s*|\s*```$/g, "");
      return JSON.parse(cleaned);
    } catch {
      return null;
    }
  }, [text, closed]);

  if (!closed) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        正在生成图表…
      </div>
    );
  }
  if (!spec || !Array.isArray(spec.data)) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        图表数据解析失败
      </div>
    );
  }

  const data = spec.data;
  const series: Array<{ key: string; color?: string }> =
    spec.series ??
    (chartType === "pie"
      ? []
      : Object.keys(data[0] || {})
          .filter((k) => k !== "name" && typeof data[0][k] === "number")
          .map((k) => ({ key: k })));

  const axis = {
    stroke: "hsl(var(--muted-foreground))",
    fontSize: 11,
    tickLine: false,
  } as const;
  const tooltipStyle = {
    background: "hsl(var(--card))",
    border: "1px solid hsl(var(--border))",
    borderRadius: 8,
    fontSize: 12,
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="overflow-hidden rounded-xl border border-border bg-card"
    >
      <div className="flex items-center gap-2 border-b border-border/60 px-3 py-2">
        <BarChart3 className="h-3.5 w-3.5 text-primary" />
        <p className="text-xs font-semibold text-foreground">
          {title || "数据图表"}
        </p>
        {onOpenCanvas && (
          <button
            onClick={() => onOpenCanvas({ kind: "chart", title, chartType, spec })}
            title="在画布中打开"
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border bg-background px-1.5 py-0.5 text-[10px] text-foreground hover:border-primary/40 hover:text-primary"
          >
            <PanelRightOpen className="h-3 w-3" /> 画布
          </button>
        )}
      </div>
      <div className="h-64 w-full p-3">
        <ResponsiveContainer width="100%" height="100%">
          {chartType === "line" ? (
            <LineChart data={data} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="name" {...axis} />
              <YAxis {...axis} />
              <Tooltip contentStyle={tooltipStyle} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
              {series.map((s, i) => (
                <Line
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  stroke={s.color || CHART_PALETTE[i % CHART_PALETTE.length]}
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  activeDot={{ r: 5 }}
                />
              ))}
            </LineChart>
          ) : chartType === "bar" ? (
            <BarChart data={data} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="name" {...axis} />
              <YAxis {...axis} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "hsl(var(--muted))" }} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
              {series.map((s, i) => (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  fill={s.color || CHART_PALETTE[i % CHART_PALETTE.length]}
                  radius={[4, 4, 0, 0]}
                />
              ))}
            </BarChart>
          ) : chartType === "area" ? (
            <AreaChart data={data} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="name" {...axis} />
              <YAxis {...axis} />
              <Tooltip contentStyle={tooltipStyle} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
              {series.map((s, i) => {
                const color = s.color || CHART_PALETTE[i % CHART_PALETTE.length];
                return (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    stroke={color}
                    fill={color}
                    fillOpacity={0.18}
                    strokeWidth={2}
                  />
                );
              })}
            </AreaChart>
          ) : (
            <PieChart>
              <Tooltip contentStyle={tooltipStyle} />
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                outerRadius={90}
                innerRadius={45}
                paddingAngle={2}
              >
                {data.map((_: any, i: number) => (
                  <Cell key={i} fill={CHART_PALETTE[i % CHART_PALETTE.length]} />
                ))}
              </Pie>
              <Legend wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          )}
        </ResponsiveContainer>
      </div>
    </motion.div>
  );
}

function TextBlock({ text }: { text: string }) {
  const md = preprocessCitations(text);
  return (
    <div className="prose prose-sm max-w-none break-words text-foreground prose-headings:font-semibold prose-headings:text-foreground prose-p:my-2 prose-p:leading-relaxed prose-strong:text-foreground prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:font-normal prose-code:before:content-none prose-code:after:content-none prose-ol:my-2 prose-ul:my-2 prose-li:my-0.5 prose-table:text-sm">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            if (href?.startsWith("#cite:")) {
              const source = decodeURIComponent(href.slice(6));
              return <CitationChip source={source}>{children}</CitationChip>;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" className="text-primary underline-offset-2 hover:underline">
                {children}
              </a>
            );
          },
          pre({ children }) {
            return <CodeBlock>{children}</CodeBlock>;
          },
        }}
      >
        {md}
      </ReactMarkdown>
    </div>
  );
}

function CitationChip({ source, children }: { source: string; children: any }) {
  return (
    <span
      title={`来源: ${source}`}
      className="mx-0.5 inline-flex items-center gap-1 rounded-md border border-primary/20 bg-primary/5 px-1.5 py-0 align-baseline text-[11px] font-medium text-primary no-underline hover:bg-primary/10"
    >
      <Database className="h-2.5 w-2.5" />
      {children}
    </span>
  );
}

function CodeBlock({ children }: { children: any }) {
  const [copied, setCopied] = useState(false);
  const codeText =
    (children?.props?.children as string) ??
    (typeof children === "string" ? children : "");
  return (
    <div className="group relative my-3 overflow-hidden rounded-lg border border-border bg-[hsl(220_15%_15%)]">
      <button
        onClick={() => {
          navigator.clipboard.writeText(String(codeText));
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        }}
        className="absolute right-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-md bg-white/5 text-white/70 opacity-0 transition-opacity hover:bg-white/10 hover:text-white group-hover:opacity-100"
        title="复制代码"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <pre className="!my-0 !bg-transparent overflow-x-auto p-4 text-[12.5px] leading-relaxed text-white/90">
        {children}
      </pre>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Test Cases — grounded, multi-agent generated test case wall
// ─────────────────────────────────────────────────────────────

function priorityTone(p?: string): { bg: string; fg: string; ring: string } {
  const k = (p || "").toUpperCase();
  if (k === "P0" || k === "CRITICAL" || k === "高")
    return { bg: "bg-red-500/10", fg: "text-red-600 dark:text-red-400", ring: "ring-red-500/30" };
  if (k === "P1" || k === "HIGH")
    return { bg: "bg-orange-500/10", fg: "text-orange-600 dark:text-orange-400", ring: "ring-orange-500/30" };
  if (k === "P2" || k === "MED" || k === "MEDIUM" || k === "中")
    return { bg: "bg-amber-500/10", fg: "text-amber-600 dark:text-amber-400", ring: "ring-amber-500/30" };
  return { bg: "bg-muted", fg: "text-muted-foreground", ring: "ring-border" };
}

function typeTone(t?: string): string {
  const k = (t || "").toLowerCase();
  if (k.includes("异常") || k.includes("negative") || k.includes("错误"))
    return "bg-rose-500/10 text-rose-600 dark:text-rose-400";
  if (k.includes("边界") || k.includes("boundary"))
    return "bg-violet-500/10 text-violet-600 dark:text-violet-400";
  if (k.includes("回归") || k.includes("regression"))
    return "bg-sky-500/10 text-sky-600 dark:text-sky-400";
  if (k.includes("性能") || k.includes("perf"))
    return "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400";
  if (k.includes("安全") || k.includes("security"))
    return "bg-pink-500/10 text-pink-600 dark:text-pink-400";
  return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
}

function csvEscape(v: string): string {
  if (v == null) return "";
  const s = String(v);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function toCSV(items: TestCaseItem[]): string {
  const headers = [
    "ID",
    "Title",
    "Priority",
    "Type",
    "Preconditions",
    "Steps",
    "Expected",
    "Data",
    "Linked Defect",
    "Linked Req",
    "Rationale",
    "Tags",
  ];
  const lines = [headers.join(",")];
  for (const it of items) {
    lines.push(
      [
        it.id,
        it.title,
        it.priority || "",
        it.type || "",
        (it.preconditions || []).join(" | "),
        (it.steps || []).map((s, i) => `${i + 1}. ${s}`).join(" | "),
        (it.expected || []).map((s, i) => `${i + 1}. ${s}`).join(" | "),
        it.data || "",
        it.linked_defect || "",
        it.linked_req || "",
        it.rationale || "",
        (it.tags || []).join(";"),
      ]
        .map(csvEscape)
        .join(","),
    );
  }
  return "\ufeff" + lines.join("\n");
}

function toMarkdown(items: TestCaseItem[]): string {
  return items
    .map((it) => {
      const lines: string[] = [];
      lines.push(`### ${it.id} · ${it.title}`);
      const meta = [it.priority && `**${it.priority}**`, it.type && `_${it.type}_`].filter(Boolean).join(" · ");
      if (meta) lines.push(meta);
      if (it.rationale) lines.push(`> 依据：${it.rationale}`);
      if (it.preconditions?.length) {
        lines.push("**前置条件**");
        it.preconditions.forEach((s) => lines.push(`- ${s}`));
      }
      if (it.steps?.length) {
        lines.push("**操作步骤**");
        it.steps.forEach((s, i) => lines.push(`${i + 1}. ${s}`));
      }
      if (it.expected?.length) {
        lines.push("**预期结果**");
        it.expected.forEach((s, i) => lines.push(`${i + 1}. ${s}`));
      }
      if (it.data) lines.push(`**测试数据**：${it.data}`);
      const refs = [
        it.linked_req && `需求 ${it.linked_req}`,
        it.linked_defect && `缺陷 ${it.linked_defect}`,
      ].filter(Boolean).join(" · ");
      if (refs) lines.push(`_${refs}_`);
      return lines.join("\n");
    })
    .join("\n\n---\n\n");
}



function toXLSX(items: TestCaseItem[], module?: string, title?: string): ArrayBuffer {
  const headers = [
    "ID", "标题", "优先级", "类型", "前置条件", "操作步骤", "预期结果",
    "测试数据", "关联缺陷", "关联需求", "生成依据", "标签",
  ];
  const rows = items.map((it) => [
    it.id, it.title, it.priority || "", it.type || "",
    (it.preconditions || []).join("\n"),
    (it.steps || []).map((s, i) => `${i + 1}. ${s}`).join("\n"),
    (it.expected || []).map((s, i) => `${i + 1}. ${s}`).join("\n"),
    it.data || "", it.linked_defect || "", it.linked_req || "",
    it.rationale || "", (it.tags || []).join("; "),
  ]);
  const ws = XLSX.utils.aoa_to_sheet([headers, ...rows]);
  ws["!cols"] = [
    { wch: 10 }, { wch: 36 }, { wch: 8 }, { wch: 10 }, { wch: 28 },
    { wch: 40 }, { wch: 36 }, { wch: 22 }, { wch: 14 }, { wch: 14 },
    { wch: 42 }, { wch: 16 },
  ];
  // Enable wrap text on body rows
  const range = XLSX.utils.decode_range(ws["!ref"] || "A1");
  for (let R = 1; R <= range.e.r; ++R) {
    for (let C = 0; C <= range.e.c; ++C) {
      const addr = XLSX.utils.encode_cell({ r: R, c: C });
      if (ws[addr]) ws[addr].s = { alignment: { wrapText: true, vertical: "top" } };
    }
  }
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "TestCases");
  // Summary sheet
  const summary = [
    ["报告标题", title || "测试用例报告"],
    ["关联模块", module || "-"],
    ["用例总数", items.length],
    ["生成时间", new Date().toLocaleString("zh-CN")],
    [],
    ["优先级分布", ""],
    ...Object.entries(
      items.reduce<Record<string, number>>((acc, it) => {
        const k = it.priority || "未标注"; acc[k] = (acc[k] || 0) + 1; return acc;
      }, {})
    ),
    [],
    ["类型分布", ""],
    ...Object.entries(
      items.reduce<Record<string, number>>((acc, it) => {
        const k = it.type || "未标注"; acc[k] = (acc[k] || 0) + 1; return acc;
      }, {})
    ),
  ];
  const wsSum = XLSX.utils.aoa_to_sheet(summary);
  wsSum["!cols"] = [{ wch: 20 }, { wch: 32 }];
  XLSX.utils.book_append_sheet(wb, wsSum, "Summary");
  return XLSX.write(wb, { bookType: "xlsx", type: "array" });
}

function toPDF(items: TestCaseItem[], module?: string, title?: string): Blob {
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const pageW = doc.internal.pageSize.getWidth();
  const now = new Date().toLocaleString("zh-CN");

  // Header
  doc.setFillColor(15, 23, 42);
  doc.rect(0, 0, pageW, 64, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFontSize(16);
  doc.text(title || "Test Case Report", 40, 30);
  doc.setFontSize(9);
  doc.setTextColor(200, 210, 230);
  doc.text(
    `Module: ${module || "-"}   |   Total: ${items.length}   |   Generated: ${now}`,
    40, 50,
  );

  // Summary block
  doc.setTextColor(15, 23, 42);
  doc.setFontSize(11);
  doc.text("Summary", 40, 92);
  const prioCount = items.reduce<Record<string, number>>((a, it) => {
    const k = it.priority || "N/A"; a[k] = (a[k] || 0) + 1; return a;
  }, {});
  const typeCount = items.reduce<Record<string, number>>((a, it) => {
    const k = it.type || "N/A"; a[k] = (a[k] || 0) + 1; return a;
  }, {});
  doc.setFontSize(9);
  doc.setTextColor(60, 70, 90);
  doc.text(
    `Priority: ${Object.entries(prioCount).map(([k, v]) => `${k}=${v}`).join("  ")}`,
    40, 108,
  );
  doc.text(
    `Type: ${Object.entries(typeCount).map(([k, v]) => `${k}=${v}`).join("  ")}`,
    40, 122,
  );

  // Table of cases
  autoTable(doc, {
    startY: 140,
    head: [["ID", "Title", "Priority", "Type", "Rationale"]],
    body: items.map((it) => [
      it.id, it.title, it.priority || "", it.type || "", it.rationale || "",
    ]),
    styles: { fontSize: 8, cellPadding: 4, valign: "top" },
    headStyles: { fillColor: [30, 41, 59], textColor: 255 },
    alternateRowStyles: { fillColor: [248, 250, 252] },
    columnStyles: {
      0: { cellWidth: 60 }, 1: { cellWidth: 150 },
      2: { cellWidth: 50 }, 3: { cellWidth: 50 }, 4: { cellWidth: 200 },
    },
    margin: { left: 40, right: 40 },
  });

  // Per-case detail cards
  items.forEach((it) => {
    doc.addPage();
    let y = 50;
    doc.setFillColor(241, 245, 249);
    doc.rect(40, y - 30, pageW - 80, 44, "F");
    doc.setFontSize(13);
    doc.setTextColor(15, 23, 42);
    doc.text(`${it.id}  ${it.title}`, 50, y - 10);
    doc.setFontSize(9);
    doc.setTextColor(80, 90, 110);
    doc.text(
      [it.priority && `Priority: ${it.priority}`, it.type && `Type: ${it.type}`,
       it.linked_req && `Req: ${it.linked_req}`, it.linked_defect && `Defect: ${it.linked_defect}`]
        .filter(Boolean).join("   |   "),
      50, y + 6,
    );
    y += 30;

    const section = (label: string, lines: string[]) => {
      if (!lines.length) return;
      doc.setFontSize(10);
      doc.setTextColor(15, 23, 42);
      doc.text(label, 40, y); y += 14;
      doc.setFontSize(9);
      doc.setTextColor(50, 60, 80);
      lines.forEach((l) => {
        const wrapped = doc.splitTextToSize(l, pageW - 100);
        doc.text(wrapped, 50, y);
        y += wrapped.length * 12;
      });
      y += 6;
    };

    if (it.rationale) section("Rationale (生成依据)", [it.rationale]);
    if (it.preconditions?.length) section("Preconditions", it.preconditions);
    if (it.steps?.length) section("Steps", it.steps.map((s, i) => `${i + 1}. ${s}`));
    if (it.expected?.length) section("Expected", it.expected.map((s, i) => `${i + 1}. ${s}`));
    if (it.data) section("Test Data", [it.data]);
    if (it.tags?.length) section("Tags", [it.tags.join(", ")]);
  });

  return doc.output("blob");
}


function TestCasesBlock({
  module,
  title,
  items,
  raw,
  closed,
}: {
  module?: string;
  title?: string;
  items: TestCaseItem[];
  raw: string;
  closed: boolean;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const flash = (k: string) => { setCopied(k); setTimeout(() => setCopied(null), 1400); };

  if (!closed && items.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-dashed border-border bg-card/40 px-3 py-3 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
        正在综合历史缺陷与覆盖率缺口，生成测试用例…
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
        测试用例解析失败，原始内容：
        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10px]">
          {raw.slice(0, 600)}
        </pre>
      </div>
    );
  }

  const stamp = new Date().toISOString().slice(0, 10);
  const fname = (ext: string) => `testcases_${module || "module"}_${stamp}.${ext}`;
  const saveBlob = (blob: Blob, ext: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = fname(ext); a.click();
    URL.revokeObjectURL(url);
  };

  const exportCSV = () => {
    saveBlob(new Blob([toCSV(items)], { type: "text/csv;charset=utf-8;" }), "csv");
    flash("csv");
  };
  const exportXLSX = () => {
    const buf = toXLSX(items, module, title);
    saveBlob(new Blob([buf], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }), "xlsx");
    flash("xlsx");
  };
  const exportPDF = () => {
    saveBlob(toPDF(items, module, title), "pdf");
    flash("pdf");
  };
  const copyMD = async () => {
    await navigator.clipboard.writeText(toMarkdown(items));
    flash("md");
  };

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-gradient-to-b from-card to-card/60">
      <div className="flex items-center gap-2 border-b border-border/60 bg-muted/30 px-3 py-2">
        <div className="flex h-6 w-6 items-center justify-center rounded-md bg-primary/10 text-primary">
          <ClipboardList className="h-3.5 w-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold text-foreground">
            {title || "生成测试用例"}
            {module && (
              <span className="ml-2 rounded-full bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">
                {module}
              </span>
            )}
          </p>
          <p className="text-[10px] text-muted-foreground">
            共 {items.length} 条 · 已与历史缺陷 / 需求 / 覆盖率缺口接地
          </p>
        </div>
        <button
          onClick={copyMD}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-[10px] text-foreground transition-colors hover:border-primary/40 hover:text-primary"
        >
          {copied === "md" ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          Markdown
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-[10px] text-foreground transition-colors hover:border-primary/40 hover:text-primary">
              {copied && copied !== "md" ? <Check className="h-3 w-3" /> : <Download className="h-3 w-3" />}
              导出
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="text-[10px] text-muted-foreground">
              导出 {items.length} 条用例
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={exportCSV} className="gap-2 text-xs">
              <FileText className="h-3.5 w-3.5 text-muted-foreground" />
              <div className="flex flex-col">
                <span>CSV</span>
                <span className="text-[10px] text-muted-foreground">原始数据 · UTF-8 BOM</span>
              </div>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={exportXLSX} className="gap-2 text-xs">
              <FileSpreadsheet className="h-3.5 w-3.5 text-emerald-600" />
              <div className="flex flex-col">
                <span>Excel (XLSX)</span>
                <span className="text-[10px] text-muted-foreground">含摘要表 · 自动换行</span>
              </div>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={exportPDF} className="gap-2 text-xs">
              <FileText className="h-3.5 w-3.5 text-rose-600" />
              <div className="flex flex-col">
                <span>PDF 报告</span>
                <span className="text-[10px] text-muted-foreground">摘要 + 用例卡片含依据</span>
              </div>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="grid gap-2 p-2 sm:grid-cols-2">
        {items.map((tc) => {
          const tone = priorityTone(tc.priority);
          return (
            <div
              key={tc.id}
              className={`group/tc relative flex flex-col gap-2 rounded-lg border border-border bg-background p-3 ring-1 ring-transparent transition-all hover:border-primary/40 hover:ring-primary/10`}
            >
              <div className="flex items-start gap-2">
                <span className="font-mono text-[10px] font-medium text-muted-foreground">{tc.id}</span>
                {tc.priority && (
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ring-1 ${tone.bg} ${tone.fg} ${tone.ring}`}
                  >
                    {tc.priority}
                  </span>
                )}
                {tc.type && (
                  <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${typeTone(tc.type)}`}>
                    {tc.type}
                  </span>
                )}
              </div>
              <p className="text-sm font-semibold leading-snug text-foreground">{tc.title}</p>
              {tc.rationale && (
                <p className="rounded-md border-l-2 border-primary/40 bg-primary/5 px-2 py-1 text-[11px] leading-relaxed text-muted-foreground">
                  <span className="mr-1 font-medium text-primary">依据</span>
                  {tc.rationale}
                </p>
              )}
              {tc.preconditions?.length ? (
                <div>
                  <p className="mb-0.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">前置</p>
                  <ul className="space-y-0.5 text-[11px] text-foreground">
                    {tc.preconditions.map((s, i) => (
                      <li key={i} className="flex gap-1.5">
                        <span className="text-muted-foreground">·</span>
                        <span>{s}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {tc.steps?.length ? (
                <div>
                  <p className="mb-0.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">步骤</p>
                  <ol className="space-y-0.5 text-[11px] text-foreground">
                    {tc.steps.map((s, i) => (
                      <li key={i} className="flex gap-1.5">
                        <span className="mt-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-muted text-[9px] font-medium text-muted-foreground">
                          {i + 1}
                        </span>
                        <span>{s}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              ) : null}
              {tc.expected?.length ? (
                <div>
                  <p className="mb-0.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">预期</p>
                  <ul className="space-y-0.5 text-[11px] text-foreground">
                    {tc.expected.map((s, i) => (
                      <li key={i} className="flex gap-1.5">
                        <Check className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
                        <span>{s}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {tc.data && (
                <p className="rounded-md bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                  数据 · {tc.data}
                </p>
              )}
              {(tc.linked_defect || tc.linked_req || tc.tags?.length) && (
                <div className="mt-auto flex flex-wrap items-center gap-1 border-t border-border/60 pt-2">
                  {tc.linked_req && (
                    <span className="inline-flex items-center gap-1 rounded-md bg-sky-500/10 px-1.5 py-0.5 text-[10px] text-sky-600 dark:text-sky-400">
                      <FileText className="h-2.5 w-2.5" />
                      {tc.linked_req}
                    </span>
                  )}
                  {tc.linked_defect && (
                    <span className="inline-flex items-center gap-1 rounded-md bg-rose-500/10 px-1.5 py-0.5 text-[10px] text-rose-600 dark:text-rose-400">
                      <Bug className="h-2.5 w-2.5" />
                      {tc.linked_defect}
                    </span>
                  )}
                  {tc.tags?.map((t) => (
                    <span key={t} className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      #{t}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
