import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Download, Play, Loader2, Maximize2, Minimize2, FileText, Image as ImageIcon } from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { duckdbManager } from "@/lib/duckdb/client";

export type Artifact =
  | { kind: "chart"; title?: string; chartType: "line" | "bar" | "area" | "pie"; spec: any }
  | { kind: "sql"; title?: string; sql: string; columns: string[]; rows: any[]; rowCount: number }
  | { kind: "python"; title?: string; stdout?: string; value?: string; figures?: string[] };

interface Props {
  artifact: Artifact | null;
  onClose: () => void;
}

const PALETTE = ["hsl(var(--primary))", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16"];

export default function ArtifactCanvas({ artifact, onClose }: Props) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { if (!artifact) setExpanded(false); }, [artifact]);

  return (
    <AnimatePresence>
      {artifact && (
        <motion.aside
          initial={{ x: "100%" }}
          animate={{ x: 0 }}
          exit={{ x: "100%" }}
          transition={{ type: "spring", stiffness: 320, damping: 32 }}
          className={`flex shrink-0 flex-col border-l border-border bg-background ${
            expanded ? "w-[min(900px,70vw)]" : "w-[min(560px,50vw)]"
          }`}
        >
          <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
            <div className="flex h-6 w-6 items-center justify-center rounded-md bg-primary/10 text-primary">
              {artifact.kind === "chart" ? <ImageIcon className="h-3.5 w-3.5" /> :
               artifact.kind === "python" ? <FileText className="h-3.5 w-3.5" /> :
               <Play className="h-3.5 w-3.5" />}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-foreground">
                {artifact.title || (artifact.kind === "sql" ? "SQL 结果" : artifact.kind === "chart" ? "图表" : "Python 结果")}
              </p>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">画布 · Artifact</p>
            </div>
            <button
              onClick={() => setExpanded((v) => !v)}
              title={expanded ? "收起" : "展开"}
              className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            </button>
            <button
              onClick={onClose}
              className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </header>
          <div className="flex-1 overflow-y-auto">
            {artifact.kind === "chart" && <ChartCanvas a={artifact} />}
            {artifact.kind === "sql" && <SqlCanvas a={artifact} />}
            {artifact.kind === "python" && <PythonCanvas a={artifact} />}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function ChartCanvas({ a }: { a: Extract<Artifact, { kind: "chart" }> }) {
  const data = Array.isArray(a.spec?.data) ? a.spec.data : [];
  const series: Array<{ key: string; color?: string }> =
    a.spec?.series ??
    (a.chartType === "pie"
      ? []
      : Object.keys(data[0] || {})
          .filter((k) => k !== "name" && typeof data[0][k] === "number")
          .map((k) => ({ key: k })));
  const axis = { stroke: "hsl(var(--muted-foreground))", fontSize: 11, tickLine: false } as const;
  const tooltipStyle = { background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 };

  const exportCsv = () => {
    if (!data.length) return;
    const cols = Object.keys(data[0]);
    const lines = [cols.join(",")];
    for (const r of data) lines.push(cols.map((c) => csvCell(r[c])).join(","));
    download(`${slug(a.title)}.csv`, lines.join("\n"), "text/csv");
  };
  const exportJson = () => download(`${slug(a.title)}.json`, JSON.stringify(a.spec, null, 2), "application/json");

  return (
    <div className="p-4">
      <div className="mb-3 flex gap-1.5">
        <CanvasBtn icon={Download} label="CSV" onClick={exportCsv} />
        <CanvasBtn icon={Download} label="JSON" onClick={exportJson} />
      </div>
      <div className="h-[420px] w-full rounded-lg border border-border bg-card p-3">
        <ResponsiveContainer width="100%" height="100%">
          {a.chartType === "line" ? (
            <LineChart data={data} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="name" {...axis} /><YAxis {...axis} /><Tooltip contentStyle={tooltipStyle} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
              {series.map((s, i) => (
                <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color || PALETTE[i % PALETTE.length]} strokeWidth={2} dot={{ r: 3 }} />
              ))}
            </LineChart>
          ) : a.chartType === "bar" ? (
            <BarChart data={data} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="name" {...axis} /><YAxis {...axis} /><Tooltip contentStyle={tooltipStyle} cursor={{ fill: "hsl(var(--muted))" }} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
              {series.map((s, i) => (
                <Bar key={s.key} dataKey={s.key} fill={s.color || PALETTE[i % PALETTE.length]} radius={[4, 4, 0, 0]} />
              ))}
            </BarChart>
          ) : a.chartType === "area" ? (
            <AreaChart data={data} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="name" {...axis} /><YAxis {...axis} /><Tooltip contentStyle={tooltipStyle} />
              {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
              {series.map((s, i) => {
                const color = s.color || PALETTE[i % PALETTE.length];
                return <Area key={s.key} type="monotone" dataKey={s.key} stroke={color} fill={color} fillOpacity={0.18} strokeWidth={2} />;
              })}
            </AreaChart>
          ) : (
            <PieChart>
              <Tooltip contentStyle={tooltipStyle} />
              <Pie data={data} dataKey="value" nameKey="name" outerRadius={120} innerRadius={60} paddingAngle={2}>
                {data.map((_: any, i: number) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
              </Pie>
              <Legend wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          )}
        </ResponsiveContainer>
      </div>
      <details className="mt-3 rounded-lg border border-border bg-card">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted-foreground">原始数据 ({data.length} 行)</summary>
        <pre className="overflow-x-auto px-3 pb-3 font-mono text-[11px] text-foreground">{JSON.stringify(data, null, 2)}</pre>
      </details>
    </div>
  );
}

function SqlCanvas({ a }: { a: Extract<Artifact, { kind: "sql" }> }) {
  const [sql, setSql] = useState(a.sql);
  const [rows, setRows] = useState(a.rows);
  const [cols, setCols] = useState(a.columns);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ms, setMs] = useState<number | null>(null);

  const run = async () => {
    setRunning(true); setErr(null);
    try {
      const r = await duckdbManager.runQuery(sql, 500);
      setRows(r.rows); setCols(r.columns); setMs(r.ms);
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setRunning(false); }
  };

  const exportCsv = () => {
    if (!rows.length) return;
    const lines = [cols.join(",")];
    for (const r of rows) lines.push(cols.map((c) => csvCell(r[c])).join(","));
    download(`query.csv`, lines.join("\n"), "text/csv");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <textarea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          spellCheck={false}
          rows={6}
          className="w-full resize-y rounded-md border border-border bg-card p-2 font-mono text-[12px] leading-relaxed text-foreground outline-none focus:border-primary/50"
        />
        <div className="mt-2 flex items-center gap-1.5">
          <button
            onClick={run} disabled={running}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {running ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
            运行
          </button>
          <CanvasBtn icon={Download} label="CSV" onClick={exportCsv} />
          <span className="ml-auto font-mono text-[10px] text-muted-foreground">
            {rows.length} 行 {ms != null && `· ${ms}ms`}
          </span>
        </div>
        {err && <p className="mt-2 rounded-md bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">{err}</p>}
      </div>
      <div className="flex-1 overflow-auto p-3">
        <table className="w-full border-collapse text-[11px]">
          <thead className="sticky top-0 bg-background">
            <tr className="border-b border-border text-left text-muted-foreground">
              {cols.map((c) => <th key={c} className="py-1.5 pr-3 font-medium">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-border/40 hover:bg-muted/30">
                {cols.map((c) => <td key={c} className="py-1 pr-3 font-mono text-foreground">{formatCell(r[c])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <p className="text-center text-xs text-muted-foreground">空结果</p>}
      </div>
    </div>
  );
}

function PythonCanvas({ a }: { a: Extract<Artifact, { kind: "python" }> }) {
  return (
    <div className="space-y-3 p-4">
      {a.stdout && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">stdout</p>
          <pre className="overflow-x-auto whitespace-pre-wrap rounded-md border border-border bg-card p-3 font-mono text-[11px] text-foreground">{a.stdout}</pre>
        </div>
      )}
      {a.value && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">返回值</p>
          <pre className="overflow-x-auto whitespace-pre-wrap rounded-md border border-border bg-card p-3 font-mono text-[11px] text-muted-foreground">{a.value}</pre>
        </div>
      )}
      {a.figures?.map((b64, i) => (
        <div key={i}>
          <div className="mb-1 flex items-center justify-between">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">figure {i + 1}</p>
            <button
              onClick={() => downloadDataUrl(`figure-${i + 1}.png`, `data:image/png;base64,${b64}`)}
              className="inline-flex items-center gap-1 text-[10px] text-primary hover:underline"
            >
              <Download className="h-3 w-3" /> PNG
            </button>
          </div>
          <img src={`data:image/png;base64,${b64}`} alt="" className="w-full rounded-md border border-border" />
        </div>
      ))}
    </div>
  );
}

function CanvasBtn({ icon: Icon, label, onClick }: { icon: any; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-[11px] text-foreground hover:bg-muted">
      <Icon className="h-3 w-3" /> {label}
    </button>
  );
}

function csvCell(v: unknown): string {
  if (v == null) return "";
  let s: string;
  if (typeof v === "bigint") s = v.toString();
  else if (v instanceof Date) s = v.toISOString();
  else if (typeof v === "object") s = JSON.stringify(v);
  else s = String(v);
  if (/[",\n\r]/.test(s)) s = `"${s.replace(/"/g, '""')}"`;
  return s;
}
function formatCell(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toString() : v.toFixed(3);
  const s = String(v);
  return s.length > 80 ? s.slice(0, 77) + "…" : s;
}
function slug(s?: string) {
  return (s || "chart").replace(/[^a-zA-Z0-9_\u4e00-\u9fa5-]+/g, "_").slice(0, 40);
}
function download(name: string, body: string, mime: string) {
  const blob = new Blob([body], { type: mime });
  const url = URL.createObjectURL(blob);
  downloadDataUrl(name, url);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function downloadDataUrl(name: string, url: string) {
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
}
