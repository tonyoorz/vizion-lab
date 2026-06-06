import { useEffect, useRef, useState } from "react";
import { Database, Loader2, X, ChevronDown, Table as TableIcon } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { duckdbManager, TableInfo } from "@/lib/duckdb/client";

interface Props {
  tables: TableInfo[];
  loading: boolean;
}

/** Persistent data status HUD — chip + click-to-open schema browser. */
export default function DataHud({ tables, loading }: Props) {
  const [open, setOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const [activeTable, setActiveTable] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!popRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    if (!activeTable && tables[0]) setActiveTable(tables[0].name);
  }, [tables, activeTable]);

  const totalRows = tables.reduce((s, t) => s + t.rows, 0);
  const empty = tables.length === 0 && !loading;
  const current = tables.find((t) => t.name === activeTable);

  return (
    <div className="relative" ref={popRef}>
      <button
        onClick={() => !empty && setOpen((v) => !v)}
        className={`flex items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-[11px] transition-colors ${
          empty
            ? "cursor-default border-dashed border-border text-muted-foreground"
            : "border-border hover:bg-muted"
        }`}
        title={empty ? "拖拽 .csv / .parquet / .duckdb 到聊天框接入数据" : "查看已连接的数据"}
      >
        {loading ? (
          <Loader2 className="h-3 w-3 animate-spin text-primary" />
        ) : empty ? (
          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
        ) : (
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
          </span>
        )}
        <Database className="h-3 w-3 text-muted-foreground" />
        {loading ? (
          <span className="font-medium text-foreground">加载本地数据…</span>
        ) : empty ? (
          <span>未连接数据 · 拖拽文件接入</span>
        ) : (
          <>
            <span className="font-medium text-foreground">
              {tables.length} 表
            </span>
            <span className="text-muted-foreground">
              · {totalRows.toLocaleString()} 行 · 本地
            </span>
            <ChevronDown
              className={`h-3 w-3 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
            />
          </>
        )}
        {!empty && !loading && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (confirm("断开本地 DuckDB 数据？")) {
                duckdbManager.reset();
                setOpen(false);
                setActiveTable(null);
              }
            }}
            className="ml-1 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            title="断开"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </button>

      <AnimatePresence>
        {open && current && (
          <motion.div
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: 0.12 }}
            className="absolute right-0 top-[calc(100%+6px)] z-50 flex w-[440px] overflow-hidden rounded-xl border border-border bg-popover shadow-xl"
          >
            {/* Table list */}
            <div className="w-[150px] shrink-0 border-r border-border bg-muted/40 py-1">
              <p className="px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                表 · {tables.length}
              </p>
              <div className="max-h-[280px] overflow-y-auto">
                {tables.map((t) => (
                  <button
                    key={t.name}
                    onClick={() => setActiveTable(t.name)}
                    className={`flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-xs transition-colors ${
                      t.name === activeTable
                        ? "bg-primary/10 text-primary"
                        : "text-foreground hover:bg-muted"
                    }`}
                  >
                    <TableIcon className="h-3 w-3 shrink-0" />
                    <span className="truncate font-mono">{t.name}</span>
                  </button>
                ))}
              </div>
            </div>
            {/* Schema panel */}
            <div className="min-w-0 flex-1 p-3">
              <div className="mb-2 flex items-baseline justify-between gap-2">
                <p className="truncate font-mono text-xs font-semibold text-foreground">
                  {current.name}
                </p>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                  {current.rows.toLocaleString()} 行 · {current.columns.length} 列
                </span>
              </div>
              <p
                className="mb-2 truncate text-[10px] text-muted-foreground"
                title={current.source}
              >
                来源 {current.source}
              </p>
              <div className="max-h-[230px] space-y-0.5 overflow-y-auto rounded-md border border-border bg-background/50">
                {current.columns.map((c) => (
                  <div
                    key={c.name}
                    className="flex items-center justify-between gap-3 border-b border-border/40 px-2.5 py-1 text-[11px] last:border-b-0"
                  >
                    <span className="truncate font-mono text-foreground">{c.name}</span>
                    <span className="shrink-0 font-mono text-[10px] uppercase text-muted-foreground">
                      {c.type}
                    </span>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-[10px] text-muted-foreground">
                数据完全在浏览器中处理 · 只读 · 不出端
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
