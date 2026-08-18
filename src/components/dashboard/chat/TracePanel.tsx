// Trace 详情页：调用树 + 耗时瀑布 + 每步输入输出 + 错误
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  ChevronRight,
  Clock,
  Copy,
  Database,
  Download,
  Sparkle,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { toast } from "@/hooks/use-toast";
import {
  buildSpanTree,
  clearTraces,
  deleteTrace,
  flattenTree,
  getSpans,
  listTraces,
  type SpanKind,
  type SpanNode,
  type SpanRecord,
  type TraceRecord,
} from "@/lib/trace";

interface Props {
  open: boolean;
  onClose: () => void;
}

const kindIcon = (k: SpanKind) => {
  switch (k) {
    case "llm":
      return <Sparkle className="h-3 w-3 text-primary" />;
    case "sql":
      return <Database className="h-3 w-3 text-sky-500" />;
    case "retrieval":
      return <Activity className="h-3 w-3 text-violet-500" />;
    case "tool":
      return <Wrench className="h-3 w-3 text-amber-500" />;
    default:
      return <ChevronRight className="h-3 w-3 text-muted-foreground" />;
  }
};

const kindColor: Record<SpanKind, string> = {
  agent: "bg-muted-foreground/40",
  llm: "bg-primary",
  sql: "bg-sky-500",
  tool: "bg-amber-500",
  retrieval: "bg-violet-500",
  local: "bg-emerald-500",
};

const fmtMs = (ms?: number) => (typeof ms === "number" ? (ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`) : "—");
const fmtTime = (ts: number) => new Date(ts).toLocaleString("zh-CN", { hour12: false });

export default function TracePanel({ open, onClose }: Props) {
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [spans, setSpans] = useState<SpanRecord[]>([]);
  const [selected, setSelected] = useState<SpanRecord | null>(null);

  const refresh = async () => {
    const list = await listTraces();
    setTraces(list);
    if (!activeId && list.length) setActiveId(list[0].id);
  };

  useEffect(() => {
    if (open) void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!activeId) {
      setSpans([]);
      setSelected(null);
      return;
    }
    void getSpans(activeId).then((s) => {
      setSpans(s);
      setSelected(s.find((x) => x.status === "error") ?? s[0] ?? null);
    });
  }, [activeId]);

  const trace = traces.find((t) => t.id === activeId) || null;
  const tree = useMemo(() => flattenTree(buildSpanTree(spans)), [spans]);

  const bounds = useMemo(() => {
    if (!spans.length) return { start: 0, total: 1 };
    const start = Math.min(...spans.map((s) => s.startTs));
    const end = Math.max(...spans.map((s) => s.endTs ?? s.startTs));
    return { start, total: Math.max(end - start, 1) };
  }, [spans]);

  const exportTrace = () => {
    if (!trace) return;
    const blob = new Blob([JSON.stringify({ trace, spans }, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `trace-${trace.id.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4 backdrop-blur-sm">
      <div className="flex h-[88vh] w-full max-w-[1200px] overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        {/* Trace list */}
        <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-background/40">
          <div className="flex items-center justify-between border-b border-border px-3 py-3">
            <span className="flex items-center gap-2 text-sm font-semibold">
              <Activity className="h-4 w-4 text-primary" /> Trace
            </span>
            <button
              onClick={async () => {
                await clearTraces();
                setActiveId(null);
                await refresh();
                toast({ title: "已清空 Trace" });
              }}
              className="text-muted-foreground transition-colors hover:text-rose-500"
              title="清空全部"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="flex-1 space-y-1 overflow-y-auto p-2">
            {traces.length === 0 && (
              <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                暂无记录。运行一次智能问数或测试 Agent 后即可查看完整调用链路。
              </p>
            )}
            {traces.map((t) => (
              <button
                key={t.id}
                onClick={() => setActiveId(t.id)}
                className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors ${
                  t.id === activeId
                    ? "border-primary/50 bg-primary/10"
                    : "border-transparent hover:bg-secondary"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      t.status === "error" ? "bg-rose-500" : t.status === "running" ? "bg-amber-500" : "bg-emerald-500"
                    }`}
                  />
                  <span className="truncate text-[12px] font-medium text-foreground">{t.title || t.type}</span>
                </div>
                <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                  <span>{t.type}</span>
                  <span>·</span>
                  <span>{t.spanCount} span</span>
                  <span>·</span>
                  <span className="tabular-nums">{fmtMs(t.durationMs)}</span>
                </div>
                <div className="text-[10px] text-muted-foreground/70">{fmtTime(t.startTs)}</div>
              </button>
            ))}
          </div>
        </aside>

        {/* Detail */}
        <section className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center justify-between border-b border-border px-4 py-3">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-foreground">
                {trace ? trace.title : "Trace 详情"}
              </h2>
              {trace && (
                <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
                  <Clock className="h-3 w-3" /> {fmtMs(trace.durationMs)} · {trace.spanCount} 步 ·{" "}
                  {trace.errorCount} 错误 · <span className="font-mono">{trace.id.slice(0, 8)}</span>
                </p>
              )}
            </div>
            <div className="flex items-center gap-1">
              {trace && (
                <>
                  <button onClick={exportTrace} className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary" title="导出 JSON">
                    <Download className="h-4 w-4" />
                  </button>
                  <button
                    onClick={async () => {
                      await deleteTrace(trace.id);
                      setActiveId(null);
                      await refresh();
                    }}
                    className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-rose-500"
                    title="删除此 Trace"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </>
              )}
              <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary">
                <X className="h-4 w-4" />
              </button>
            </div>
          </header>

          {trace?.error && (
            <div className="flex items-start gap-2 border-b border-rose-500/30 bg-rose-500/10 px-4 py-2 text-[12px] text-rose-600">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {trace.error}
            </div>
          )}

          <div className="flex min-h-0 flex-1">
            {/* Call tree + waterfall */}
            <div className="min-w-0 flex-1 overflow-y-auto p-3">
              {tree.length === 0 && (
                <p className="p-6 text-center text-xs text-muted-foreground">该 Trace 暂无 span。</p>
              )}
              {tree.map((n: SpanNode) => {
                const offset = ((n.startTs - bounds.start) / bounds.total) * 100;
                const width = Math.max((((n.endTs ?? n.startTs) - n.startTs) / bounds.total) * 100, 0.8);
                return (
                  <button
                    key={n.id}
                    onClick={() => setSelected(n)}
                    className={`mb-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors ${
                      selected?.id === n.id ? "bg-primary/10" : "hover:bg-secondary/70"
                    }`}
                  >
                    <div className="flex w-[46%] min-w-0 items-center gap-1.5" style={{ paddingLeft: n.depth * 14 }}>
                      {kindIcon(n.kind)}
                      <span className="truncate text-[12px] text-foreground">{n.name}</span>
                      {n.status === "error" && <AlertCircle className="h-3 w-3 shrink-0 text-rose-500" />}
                    </div>
                    <div className="relative h-3 flex-1 rounded bg-muted/50">
                      <span
                        className={`absolute top-0 h-3 rounded ${
                          n.status === "error" ? "bg-rose-500" : kindColor[n.kind]
                        } ${n.status === "running" ? "animate-pulse" : ""}`}
                        style={{ left: `${offset}%`, width: `${width}%` }}
                      />
                    </div>
                    <span className="w-14 shrink-0 text-right text-[10px] tabular-nums text-muted-foreground">
                      {fmtMs(n.durationMs)}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* IO inspector */}
            <aside className="flex w-[38%] min-w-[300px] shrink-0 flex-col border-l border-border bg-background/40">
              {!selected ? (
                <p className="p-6 text-center text-xs text-muted-foreground">选择左侧任意步骤查看输入/输出。</p>
              ) : (
                <div className="flex min-h-0 flex-1 flex-col">
                  <div className="border-b border-border px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      {kindIcon(selected.kind)}
                      <span className="text-[13px] font-medium text-foreground">{selected.name}</span>
                    </div>
                    <p className="mt-0.5 text-[10px] text-muted-foreground">
                      {selected.kind} · {fmtMs(selected.durationMs)} · {fmtTime(selected.startTs)}
                    </p>
                  </div>
                  <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
                    {selected.error && (
                      <IOBlock label="错误" text={selected.error} tone="error" />
                    )}
                    {selected.attrs && Object.keys(selected.attrs).length > 0 && (
                      <IOBlock label="属性" text={JSON.stringify(selected.attrs, null, 2)} />
                    )}
                    <IOBlock label="输入" text={selected.input ?? "（无）"} />
                    <IOBlock label="输出" text={selected.output ?? "（无）"} />
                  </div>
                </div>
              )}
            </aside>
          </div>
        </section>
      </div>
    </div>
  );
}

function IOBlock({ label, text, tone }: { label: string; text: string; tone?: "error" }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className={`text-[11px] font-medium ${tone === "error" ? "text-rose-500" : "text-muted-foreground"}`}>
          {label}
        </span>
        <button
          onClick={() => {
            navigator.clipboard.writeText(text);
            toast({ title: `${label}已复制` });
          }}
          className="text-muted-foreground hover:text-foreground"
        >
          <Copy className="h-3 w-3" />
        </button>
      </div>
      <pre
        className={`max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md p-2 font-mono text-[11px] ${
          tone === "error" ? "bg-rose-500/10 text-rose-600" : "bg-muted/40 text-muted-foreground"
        }`}
      >
        {text}
      </pre>
    </div>
  );
}
