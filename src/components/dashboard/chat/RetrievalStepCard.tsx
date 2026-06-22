import { useState } from "react";
import type { KnowledgeHit } from "@/lib/rag/client";

interface Props { hits: KnowledgeHit[]; }

const ScoreBar = ({ label, value, max }: { label: string; value: number; max: number }) => {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-1.5">
      <span className="w-10 text-[10px] text-muted-foreground">{label}</span>
      <div className="relative h-1 flex-1 rounded bg-muted">
        <div className="absolute inset-y-0 left-0 rounded bg-primary/70" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-12 text-right font-mono text-[10px] tabular-nums text-muted-foreground">
        {value.toFixed(3)}
      </span>
    </div>
  );
};

export default function RetrievalStepCard({ hits }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!hits.length) return <div className="text-[11px] text-muted-foreground">知识库无召回，跳过。</div>;
  const vMax = Math.max(...hits.map((h) => h.vec_score), 0.0001);
  const kMax = Math.max(...hits.map((h) => h.kw_score), 0.0001);
  const rMax = Math.max(...hits.map((h) => h.rrf_score), 0.0001);
  return (
    <div className="space-y-1.5">
      {hits.map((h) => {
        const open = expanded === h.id;
        return (
          <div key={h.id} className="rounded border border-border/60 bg-card/40 p-2">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[9px] uppercase text-muted-foreground">
                    {h.source_type}
                  </span>
                  <span className="truncate text-[12px] font-medium text-foreground">
                    {h.title || h.source_id}
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">{h.content}</p>
              </div>
              <button
                onClick={() => setExpanded(open ? null : h.id)}
                className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
              >
                {open ? "收起" : "展开"}
              </button>
            </div>
            <div className="mt-1.5 space-y-1">
              <ScoreBar label="vec" value={h.vec_score} max={vMax} />
              <ScoreBar label="kw" value={h.kw_score} max={kMax} />
              <ScoreBar label="rrf" value={h.rrf_score} max={rMax} />
            </div>
            {open && (
              <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 font-mono text-[11px] leading-relaxed">
                {h.content}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );
}
