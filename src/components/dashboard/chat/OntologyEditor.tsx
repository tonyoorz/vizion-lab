import { useEffect, useMemo, useState } from "react";
import { X, Plus, Trash2, RotateCcw, Save, Network, Sparkles, Database } from "lucide-react";
import { ONTOLOGY } from "@/lib/ontology";
import type { Attribute, Entity, Metric } from "@/lib/ontology";
import {
  subscribeOntology,
  persistOntology,
  resetOntology,
  getOntologySnapshot,
  updateEntity,
  upsertMetric,
  deleteMetric,
} from "@/lib/ontology/store";

interface Props {
  open: boolean;
  onClose: () => void;
}

type Tab = "entities" | "metrics" | "graph";

const TYPES: Attribute["type"][] = ["string", "number", "date", "enum", "boolean"];

const OntologyEditor = ({ open, onClose }: Props) => {
  const [tab, setTab] = useState<Tab>("entities");
  const [snap, setSnap] = useState(() => getOntologySnapshot());
  const [activeEntity, setActiveEntity] = useState(ONTOLOGY.entities[0]?.name);
  const [editingMetric, setEditingMetric] = useState<Metric | null>(null);
  const [editingMetricOriginalName, setEditingMetricOriginalName] = useState<string | null>(null);

  useEffect(() => subscribeOntology(() => setSnap(getOntologySnapshot())), []);

  if (!open) return null;

  const entity = snap.entities.find((e) => e.name === activeEntity) ?? snap.entities[0];

  const saveEntityField = <K extends keyof Entity>(key: K, value: Entity[K]) => {
    if (!entity) return;
    updateEntity(entity.name, { [key]: value } as Partial<Entity>);
  };

  const updateAttribute = (idx: number, patch: Partial<Attribute>) => {
    if (!entity) return;
    const attrs = entity.attributes.map((a, i) => (i === idx ? { ...a, ...patch } : a));
    updateEntity(entity.name, { attributes: attrs });
  };

  const addAttribute = () => {
    if (!entity) return;
    updateEntity(entity.name, {
      attributes: [
        ...entity.attributes,
        { name: "new_field", label: "新字段", type: "string", synonyms: [] },
      ],
    });
  };

  const removeAttribute = (idx: number) => {
    if (!entity) return;
    updateEntity(entity.name, { attributes: entity.attributes.filter((_, i) => i !== idx) });
  };

  const handleReset = () => {
    if (confirm("将丢弃所有本体修改并恢复默认。继续？")) resetOntology();
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 p-4 backdrop-blur-sm">
      <div className="relative mt-8 flex max-h-[88vh] w-full max-w-5xl flex-col rounded-xl border border-border bg-background shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">本体编辑器 · Ontology Studio</h2>
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              v{snap.version}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={handleReset}
              className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
              title="重置为默认本体"
            >
              <RotateCcw className="h-3 w-3" /> 重置
            </button>
            <button
              onClick={onClose}
              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-border px-5 pt-2 text-[12px]">
          {[
            { k: "entities", l: "实体", i: <Database className="h-3 w-3" /> },
            { k: "metrics", l: "指标", i: <Sparkles className="h-3 w-3" /> },
            { k: "graph", l: "关系图", i: <Network className="h-3 w-3" /> },
          ].map((t) => (
            <button
              key={t.k}
              onClick={() => setTab(t.k as Tab)}
              className={`-mb-px flex items-center gap-1 border-b-2 px-3 py-2 transition-colors ${
                tab === t.k
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {t.i}
              {t.l}
            </button>
          ))}
          <div className="ml-auto py-2 text-[11px] text-muted-foreground">
            修改即时生效，存于本地（重置可恢复默认）
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-hidden">
          {tab === "entities" && entity && (
            <div className="flex h-full">
              {/* Sidebar entity list */}
              <div className="w-48 shrink-0 overflow-y-auto border-r border-border bg-muted/20 p-2">
                {snap.entities.map((e) => (
                  <button
                    key={e.name}
                    onClick={() => setActiveEntity(e.name)}
                    className={`mb-1 w-full rounded px-2 py-2 text-left text-[12px] transition-colors ${
                      activeEntity === e.name
                        ? "bg-primary/10 text-foreground"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                    }`}
                  >
                    <div className="font-medium">{e.label}</div>
                    <div className="font-mono text-[10px] opacity-60">
                      {e.name} · {e.attributes.length} 字段
                    </div>
                  </button>
                ))}
              </div>

              {/* Entity detail */}
              <div className="flex-1 overflow-y-auto p-5">
                <div className="mb-4 grid grid-cols-2 gap-3 text-[12px]">
                  <Field label="实体名">
                    <input value={entity.name} disabled className="input-style opacity-60" />
                  </Field>
                  <Field label="物理表">
                    <input value={entity.table} disabled className="input-style opacity-60" />
                  </Field>
                  <Field label="显示标签">
                    <input
                      value={entity.label}
                      onChange={(e) => saveEntityField("label", e.target.value)}
                      className="input-style"
                    />
                  </Field>
                  <Field label="同义词（逗号分隔）">
                    <input
                      value={entity.synonyms.join(", ")}
                      onChange={(e) =>
                        saveEntityField(
                          "synonyms",
                          e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                        )
                      }
                      className="input-style"
                    />
                  </Field>
                </div>

                <div className="mb-2 flex items-center justify-between">
                  <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    字段
                  </div>
                  <button
                    onClick={addAttribute}
                    className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <Plus className="h-3 w-3" /> 添加字段
                  </button>
                </div>

                <div className="space-y-2">
                  {entity.attributes.map((a, i) => (
                    <div
                      key={i}
                      className="grid grid-cols-12 items-center gap-2 rounded border border-border bg-card/40 p-2 text-[12px]"
                    >
                      <input
                        value={a.name}
                        onChange={(e) => updateAttribute(i, { name: e.target.value })}
                        className="input-style col-span-2 font-mono text-[11px]"
                        placeholder="name"
                      />
                      <input
                        value={a.label}
                        onChange={(e) => updateAttribute(i, { label: e.target.value })}
                        className="input-style col-span-2"
                        placeholder="标签"
                      />
                      <select
                        value={a.type}
                        onChange={(e) =>
                          updateAttribute(i, { type: e.target.value as Attribute["type"] })
                        }
                        className="input-style col-span-1"
                      >
                        {TYPES.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                      <input
                        value={(a.synonyms ?? []).join(", ")}
                        onChange={(e) =>
                          updateAttribute(i, {
                            synonyms: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                          })
                        }
                        className="input-style col-span-3"
                        placeholder="同义词1, 同义词2"
                      />
                      <input
                        value={(a.values ?? []).join(", ")}
                        onChange={(e) =>
                          updateAttribute(i, {
                            values: e.target.value
                              ? e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                              : undefined,
                          })
                        }
                        className="input-style col-span-3"
                        placeholder={a.type === "enum" ? "枚举值1, 枚举值2" : "(仅enum使用)"}
                        disabled={a.type !== "enum"}
                      />
                      <button
                        onClick={() => removeAttribute(i)}
                        className="col-span-1 flex justify-center rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {tab === "metrics" && (
            <MetricsTab
              metrics={snap.metrics}
              entities={snap.entities}
              editing={editingMetric}
              editingOriginalName={editingMetricOriginalName}
              setEditing={(m, on) => {
                setEditingMetric(m);
                setEditingMetricOriginalName(on ?? null);
              }}
            />
          )}

          {tab === "graph" && <GraphTab />}
        </div>
      </div>

      <style>{`
        .input-style {
          width: 100%;
          background: hsl(var(--background));
          border: 1px solid hsl(var(--border));
          border-radius: 6px;
          padding: 4px 8px;
          font-size: 12px;
          outline: none;
        }
        .input-style:focus { border-color: hsl(var(--primary)); }
      `}</style>
    </div>
  );
};

// ----- subcomponents -----

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <label className="block">
    <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
    {children}
  </label>
);

const blankMetric = (baseEntity: string): Metric => ({
  name: "新指标",
  label: "新指标",
  description: "",
  synonyms: [],
  formula: "COUNT(*)",
  baseEntity,
  dimensions: [],
});

const MetricsTab = ({
  metrics,
  entities,
  editing,
  editingOriginalName,
  setEditing,
}: {
  metrics: Metric[];
  entities: Entity[];
  editing: Metric | null;
  editingOriginalName: string | null;
  setEditing: (m: Metric | null, originalName?: string | null) => void;
}) => {
  const dimsForEntity = (name: string) =>
    entities.find((e) => e.name === name)?.attributes.map((a) => a.name) ?? [];

  const save = () => {
    if (!editing) return;
    if (!editing.name.trim()) return;
    upsertMetric(editing, editingOriginalName ?? undefined);
    setEditing(null);
  };

  if (editing) {
    const allowedDims = dimsForEntity(editing.baseEntity);
    return (
      <div className="overflow-y-auto p-5">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-[13px] font-semibold">
            {editingOriginalName ? "编辑指标" : "新建指标"}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setEditing(null)}
              className="rounded border border-border px-3 py-1 text-[12px] text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              取消
            </button>
            <button
              onClick={save}
              className="flex items-center gap-1 rounded bg-primary px-3 py-1 text-[12px] text-primary-foreground hover:opacity-90"
            >
              <Save className="h-3 w-3" /> 保存
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="名称（唯一）">
            <input
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value }, editingOriginalName)}
              className="input-style"
            />
          </Field>
          <Field label="标签">
            <input
              value={editing.label}
              onChange={(e) =>
                setEditing({ ...editing, label: e.target.value }, editingOriginalName)
              }
              className="input-style"
            />
          </Field>
          <Field label="基础实体">
            <select
              value={editing.baseEntity}
              onChange={(e) =>
                setEditing({ ...editing, baseEntity: e.target.value }, editingOriginalName)
              }
              className="input-style"
            >
              {entities.map((ent) => (
                <option key={ent.name} value={ent.name}>
                  {ent.label} ({ent.name})
                </option>
              ))}
            </select>
          </Field>
          <Field label="可用维度（逗号分隔）">
            <input
              value={editing.dimensions.join(", ")}
              onChange={(e) =>
                setEditing(
                  {
                    ...editing,
                    dimensions: e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  },
                  editingOriginalName,
                )
              }
              className="input-style"
              placeholder={allowedDims.join(", ")}
            />
            <div className="mt-1 text-[10px] text-muted-foreground">
              可选：{allowedDims.join(", ") || "(基础实体无字段)"}
            </div>
          </Field>
          <Field label="描述">
            <input
              value={editing.description}
              onChange={(e) =>
                setEditing({ ...editing, description: e.target.value }, editingOriginalName)
              }
              className="input-style"
            />
          </Field>
          <Field label="同义词（逗号分隔）">
            <input
              value={editing.synonyms.join(", ")}
              onChange={(e) =>
                setEditing(
                  {
                    ...editing,
                    synonyms: e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  },
                  editingOriginalName,
                )
              }
              className="input-style"
            />
          </Field>
        </div>

        <div className="mt-3">
          <Field label="SQL 公式（DuckDB · 仅聚合表达式）">
            <textarea
              value={editing.formula}
              onChange={(e) =>
                setEditing({ ...editing, formula: e.target.value }, editingOriginalName)
              }
              rows={4}
              className="input-style font-mono text-[11px]"
              placeholder="SUM(CASE WHEN status='Open' THEN 1 ELSE 0 END)"
            />
          </Field>
        </div>
      </div>
    );
  }

  return (
    <div className="overflow-y-auto p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          指标 ({metrics.length})
        </div>
        <button
          onClick={() => setEditing(blankMetric(entities[0]?.name ?? "Defect"), null)}
          className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <Plus className="h-3 w-3" /> 新建指标
        </button>
      </div>
      <div className="grid gap-2">
        {metrics.map((m) => (
          <div
            key={m.name}
            className="rounded border border-border bg-card/40 p-3 text-[12px]"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{m.label}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {m.baseEntity}
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">{m.description}</div>
                <pre className="mt-2 overflow-x-auto rounded bg-muted/40 p-2 font-mono text-[11px]">
                  {m.formula}
                </pre>
                {m.synonyms.length > 0 && (
                  <div className="mt-1 text-[10px] text-muted-foreground">
                    同义词：{m.synonyms.join(", ")}
                  </div>
                )}
              </div>
              <div className="flex flex-col gap-1">
                <button
                  onClick={() => setEditing({ ...m }, m.name)}
                  className="rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  编辑
                </button>
                <button
                  onClick={() => {
                    if (confirm(`删除指标「${m.label}」？`)) deleteMetric(m.name);
                  }}
                  className="rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

// ----- Graph: simple SVG layout with circular entities + relation arrows -----
const GraphTab = () => {
  const snap = useMemo(() => getOntologySnapshot(), []);
  const W = 720;
  const H = 420;
  const cx = W / 2;
  const cy = H / 2;
  const r = 150;
  const positions = snap.entities.map((e, i, arr) => {
    const a = (i / arr.length) * Math.PI * 2 - Math.PI / 2;
    return { entity: e, x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  });
  const posMap = new Map(positions.map((p) => [p.entity.name, p] as const));

  const edges: { from: typeof positions[0]; to: typeof positions[0]; via: string }[] = [];
  for (const p of positions) {
    for (const rel of p.entity.relations ?? []) {
      const target = posMap.get(rel.target);
      if (target) edges.push({ from: p, to: target, via: rel.via });
    }
  }

  return (
    <div className="overflow-auto p-5">
      <div className="mb-3 text-[11px] text-muted-foreground">
        本体关系图（{snap.entities.length} 实体 · {edges.length} 关系 · {snap.metrics.length} 指标）
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-lg border border-border bg-card/30">
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="10"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="hsl(var(--muted-foreground))" />
          </marker>
        </defs>
        {edges.map((e, i) => {
          const mx = (e.from.x + e.to.x) / 2;
          const my = (e.from.y + e.to.y) / 2;
          return (
            <g key={i}>
              <line
                x1={e.from.x}
                y1={e.from.y}
                x2={e.to.x}
                y2={e.to.y}
                stroke="hsl(var(--muted-foreground))"
                strokeOpacity={0.5}
                strokeWidth={1.2}
                markerEnd="url(#arrow)"
              />
              <text
                x={mx}
                y={my - 4}
                fontSize={10}
                textAnchor="middle"
                fill="hsl(var(--muted-foreground))"
                className="font-mono"
              >
                {e.via}
              </text>
            </g>
          );
        })}
        {positions.map((p) => (
          <g key={p.entity.name} transform={`translate(${p.x},${p.y})`}>
            <circle r={42} fill="hsl(var(--primary) / 0.08)" stroke="hsl(var(--primary))" strokeWidth={1.5} />
            <text textAnchor="middle" y={-4} fontSize={12} fontWeight={600} fill="hsl(var(--foreground))">
              {p.entity.label}
            </text>
            <text textAnchor="middle" y={12} fontSize={9} fill="hsl(var(--muted-foreground))" className="font-mono">
              {p.entity.table}
            </text>
            <text textAnchor="middle" y={26} fontSize={9} fill="hsl(var(--muted-foreground))">
              {p.entity.attributes.length} 字段
            </text>
          </g>
        ))}
      </svg>

      <div className="mt-4 grid grid-cols-3 gap-3">
        {snap.entities.map((e) => (
          <div key={e.name} className="rounded border border-border bg-card/40 p-3 text-[11px]">
            <div className="mb-1 font-semibold">{e.label}</div>
            <div className="font-mono text-[10px] text-muted-foreground">{e.table}</div>
            <div className="mt-2 flex flex-wrap gap-1">
              {e.attributes.map((a) => (
                <span
                  key={a.name}
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                >
                  {a.name}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default OntologyEditor;
