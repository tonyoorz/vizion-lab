import { useEffect, useState } from "react";
import { Database, Loader2, Sparkles, X } from "lucide-react";
import { ingestKnowledge, seedOntology } from "@/lib/rag/client";
import { supabase } from "@/integrations/supabase/client";
import { duckdbManager } from "@/lib/duckdb/client";
import { toast } from "@/hooks/use-toast";

interface Props { open: boolean; onClose: () => void; }

interface Stats { total: number; bySource: Record<string, number>; modelVersion: string | null; }

const fetchStats = async (): Promise<Stats> => {
  const { data, error } = await supabase
    .from("knowledge_chunks")
    .select("source_type, model_version");
  if (error) throw error;
  const bySource: Record<string, number> = {};
  let modelVersion: string | null = null;
  for (const r of data ?? []) {
    bySource[r.source_type] = (bySource[r.source_type] ?? 0) + 1;
    modelVersion = (r as { model_version?: string }).model_version ?? modelVersion;
  }
  return { total: data?.length ?? 0, bySource, modelVersion };
};

export default function KnowledgeIngestionPanel({ open, onClose }: Props) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loadingStats, setLoadingStats] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async () => {
    setLoadingStats(true);
    try { setStats(await fetchStats()); }
    catch (e) { toast({ title: "读取知识库失败", description: (e as Error).message, variant: "destructive" }); }
    finally { setLoadingStats(false); }
  };

  useEffect(() => { if (open) void refresh(); }, [open]);

  const doSeedOntology = async () => {
    setBusy("ontology");
    try {
      const r = await seedOntology();
      toast({ title: "本体已灌入知识库", description: `seeded ${r.seeded_items} 条` });
      await refresh();
    } catch (e) { toast({ title: "灌入失败", description: (e as Error).message, variant: "destructive" }); }
    finally { setBusy(null); }
  };

  const doIngestDefects = async () => {
    setBusy("defects");
    try {
      const res = await duckdbManager.runQuery(
        `SELECT defect_id, severity, module, owner_team, status, recurrence_count, age_days,
                COALESCE(title,'') AS title, COALESCE(description,'') AS description
         FROM defects LIMIT 200`,
      );
      const items = res.rows.map((r) => ({
        source_type: "defect",
        source_id: String(r.defect_id),
        title: `[${r.severity}] ${r.title || r.defect_id} · ${r.module}`,
        content: `缺陷 ${r.defect_id}\n模块: ${r.module}  团队: ${r.owner_team}  状态: ${r.status}\n复现次数: ${r.recurrence_count}  存活天数: ${r.age_days}\n\n标题: ${r.title}\n描述: ${r.description}`,
        metadata: {
          severity: r.severity, module: r.module, owner_team: r.owner_team, status: r.status,
        },
      }));
      const out = await ingestKnowledge(items);
      toast({ title: "缺陷样本已灌入", description: `${out.inserted} 条 / 耗时 ${out.embed_ms}ms` });
      await refresh();
    } catch (e) { toast({ title: "缺陷灌入失败", description: (e as Error).message, variant: "destructive" }); }
    finally { setBusy(null); }
  };

  const doIngestCases = async () => {
    setBusy("cases");
    try {
      const res = await duckdbManager.runQuery(
        `SELECT case_id, suite, type, priority, module,
                COALESCE(covered_requirement,'') AS covered_requirement,
                COALESCE(title,'') AS title, COALESCE(steps,'') AS steps
         FROM test_cases LIMIT 200`,
      );
      const items = res.rows.map((r) => ({
        source_type: "testcase",
        source_id: String(r.case_id),
        title: `[${r.priority}] ${r.title || r.case_id} · ${r.module}`,
        content: `用例 ${r.case_id}\n套件: ${r.suite}  类型: ${r.type}  优先级: ${r.priority}  模块: ${r.module}\n覆盖需求: ${r.covered_requirement}\n\n标题: ${r.title}\n步骤: ${r.steps}`,
        metadata: { suite: r.suite, type: r.type, priority: r.priority, module: r.module },
      }));
      const out = await ingestKnowledge(items);
      toast({ title: "用例样本已灌入", description: `${out.inserted} 条 / 耗时 ${out.embed_ms}ms` });
      await refresh();
    } catch (e) { toast({ title: "用例灌入失败", description: (e as Error).message, variant: "destructive" }); }
    finally { setBusy(null); }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 backdrop-blur-sm">
      <div className="mt-12 w-full max-w-xl rounded-xl border border-border bg-background shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">知识库 · Hybrid RAG</h2>
          </div>
          <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-muted">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 p-5">
          <div className="rounded-lg border border-border bg-card/40 p-3 text-[12px]">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-medium">入库统计</span>
              <button
                onClick={refresh}
                disabled={loadingStats}
                className="text-[11px] text-muted-foreground hover:text-foreground"
              >
                {loadingStats ? "刷新中…" : "刷新"}
              </button>
            </div>
            {stats ? (
              <div className="space-y-1 text-muted-foreground">
                <div>总 chunks: <span className="font-mono text-foreground">{stats.total}</span></div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(stats.bySource).map(([k, v]) => (
                    <span key={k} className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
                      {k}: {v}
                    </span>
                  ))}
                  {!Object.keys(stats.bySource).length && <span className="text-[11px]">（空）</span>}
                </div>
                {stats.modelVersion && (
                  <div className="text-[10px]">模型: <span className="font-mono">{stats.modelVersion}</span></div>
                )}
              </div>
            ) : <div className="text-muted-foreground">…</div>}
          </div>

          <div className="grid gap-2">
            <ActionButton
              busy={busy === "ontology"}
              onClick={doSeedOntology}
              title="灌入本体说明"
              desc="把实体 / 指标 / 同义词文档嵌入向量库（少量、便宜）"
            />
            <ActionButton
              busy={busy === "defects"}
              onClick={doIngestDefects}
              title="从 DuckDB 抽样灌入缺陷 (≤200)"
              desc="便于按语义召回相似 / 历史缺陷"
            />
            <ActionButton
              busy={busy === "cases"}
              onClick={doIngestCases}
              title="从 DuckDB 抽样灌入用例 (≤200)"
              desc="便于按语义召回相关用例与覆盖范围"
            />
          </div>

          <p className="text-[10px] leading-relaxed text-muted-foreground">
            Embedding: google/gemini-embedding-001 @ 1536 维。所有灌入与检索都通过 Edge Function + service_role 完成，浏览器永远不直连数据库。
          </p>
        </div>
      </div>
    </div>
  );
}

function ActionButton({ busy, onClick, title, desc }: { busy: boolean; onClick: () => void; title: string; desc: string }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="flex items-start gap-3 rounded-lg border border-border bg-card/40 p-3 text-left transition-colors hover:bg-muted/60 disabled:opacity-60"
    >
      <div className="mt-0.5">
        {busy ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : <Sparkles className="h-4 w-4 text-primary" />}
      </div>
      <div className="flex-1">
        <div className="text-[13px] font-medium">{title}</div>
        <div className="mt-0.5 text-[11px] text-muted-foreground">{desc}</div>
      </div>
    </button>
  );
}
