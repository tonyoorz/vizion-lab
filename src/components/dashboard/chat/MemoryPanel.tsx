import { useEffect, useMemo, useState } from "react";
import { Brain, Check, Download, Trash2, Upload, X } from "lucide-react";
import { toast } from "@/hooks/use-toast";
import {
  KIND_LABEL,
  acceptMemory,
  addMemory,
  clearMemories,
  deleteMemory,
  listMemories,
  updateMemory,
  type MemoryKind,
  type UserMemory,
} from "@/lib/memory/user-memory";
import { clearMissions, deleteMission, listMissions, type MissionRecord } from "@/lib/memory/missions";

interface Props {
  open: boolean;
  onClose: () => void;
}

type Tab = "memory" | "pending" | "missions";

const KINDS: MemoryKind[] = ["preference", "fact", "glossary"];

export default function MemoryPanel({ open, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("memory");
  const [items, setItems] = useState<UserMemory[]>([]);
  const [missions, setMissions] = useState<MissionRecord[]>([]);
  const [draft, setDraft] = useState("");
  const [draftKind, setDraftKind] = useState<MemoryKind>("preference");
  const [editId, setEditId] = useState<string | null>(null);
  const [editVal, setEditVal] = useState("");

  const refresh = async () => {
    setItems(await listMemories());
    setMissions(await listMissions());
  };

  useEffect(() => {
    if (open) void refresh();
  }, [open]);

  const active = useMemo(() => items.filter((m) => m.status === "active"), [items]);
  const pending = useMemo(() => items.filter((m) => m.status === "pending"), [items]);

  const onAdd = async () => {
    if (!draft.trim()) return;
    await addMemory(draft, { kind: draftKind, source: "manual" });
    setDraft("");
    await refresh();
    toast({ title: "已记住" });
  };

  const onSaveEdit = async (m: UserMemory) => {
    if (editVal.trim()) await updateMemory({ ...m, text: editVal.trim() });
    setEditId(null);
    await refresh();
  };

  const exportJson = async () => {
    const payload = { memories: await listMemories(), missions: await listMissions() };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `dtsv-memory-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importJson = async (file: File) => {
    try {
      const data = JSON.parse(await file.text());
      for (const m of data.memories ?? []) {
        await addMemory(m.text, { kind: m.kind, source: m.source, status: m.status, confidence: m.confidence });
      }
      await refresh();
      toast({ title: "记忆已导入" });
    } catch (e) {
      toast({ title: "导入失败", description: (e as Error).message, variant: "destructive" });
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 backdrop-blur-sm">
      <div className="mt-10 flex max-h-[80vh] w-full max-w-2xl flex-col rounded-xl border border-border bg-background shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Brain className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">记忆中心</h2>
            <span className="text-[11px] text-muted-foreground">本地存储 · 数据不出端</span>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={exportJson} title="导出" className="rounded p-1 text-muted-foreground hover:bg-muted">
              <Download className="h-4 w-4" />
            </button>
            <label className="cursor-pointer rounded p-1 text-muted-foreground hover:bg-muted" title="导入">
              <Upload className="h-4 w-4" />
              <input
                type="file"
                accept="application/json"
                className="hidden"
                onChange={(e) => e.target.files?.[0] && importJson(e.target.files[0])}
              />
            </label>
            <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-muted">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="flex gap-1 border-b border-border px-4 py-2 text-[12px]">
          {([
            ["memory", `长期记忆 (${active.length})`],
            ["pending", `待确认 (${pending.length})`],
            ["missions", `任务记忆 (${missions.length})`],
          ] as [Tab, string][]).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`rounded px-2.5 py-1 transition-colors ${
                tab === k ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto p-5">
          {tab === "memory" && (
            <>
              <div className="flex gap-2">
                <select
                  value={draftKind}
                  onChange={(e) => setDraftKind(e.target.value as MemoryKind)}
                  className="rounded-md border border-border bg-background px-2 text-[12px]"
                >
                  {KINDS.map((k) => (
                    <option key={k} value={k}>
                      {KIND_LABEL[k]}
                    </option>
                  ))}
                </select>
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && onAdd()}
                  placeholder="例如：默认只看最近 30 天的 BMS 模块数据"
                  className="flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-[12px] outline-none focus:border-primary"
                />
                <button
                  onClick={onAdd}
                  className="rounded-md bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground"
                >
                  记住
                </button>
              </div>

              {!active.length && (
                <div className="py-8 text-center text-[12px] text-muted-foreground">
                  还没有长期记忆。加入后每次提问都会自动带上。
                </div>
              )}
              {active.map((m) => (
                <div key={m.id} className="flex items-start gap-2 rounded-lg border border-border bg-card/40 p-3">
                  <span className="mt-0.5 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {KIND_LABEL[m.kind]}
                  </span>
                  {editId === m.id ? (
                    <input
                      autoFocus
                      value={editVal}
                      onChange={(e) => setEditVal(e.target.value)}
                      onBlur={() => onSaveEdit(m)}
                      onKeyDown={(e) => e.key === "Enter" && onSaveEdit(m)}
                      className="flex-1 rounded border border-border bg-background px-2 py-1 text-[12px] outline-none"
                    />
                  ) : (
                    <button
                      onClick={() => {
                        setEditId(m.id);
                        setEditVal(m.text);
                      }}
                      className="flex-1 text-left text-[12px] leading-relaxed"
                    >
                      {m.text}
                      <span className="ml-2 text-[10px] text-muted-foreground">
                        {m.source === "auto" ? "自动" : "手动"} · 命中 {m.hits}
                      </span>
                    </button>
                  )}
                  <button
                    onClick={async () => {
                      await deleteMemory(m.id);
                      await refresh();
                    }}
                    className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
              {active.length > 0 && (
                <button
                  onClick={async () => {
                    if (!confirm("清空全部长期记忆？")) return;
                    await clearMemories();
                    await refresh();
                  }}
                  className="text-[11px] text-muted-foreground hover:text-destructive"
                >
                  清空全部记忆
                </button>
              )}
            </>
          )}

          {tab === "pending" && (
            <>
              {!pending.length && (
                <div className="py-8 text-center text-[12px] text-muted-foreground">
                  暂无待确认候选。对话结束后系统会自动抽取候选记忆。
                </div>
              )}
              {pending.map((m) => (
                <div key={m.id} className="flex items-start gap-2 rounded-lg border border-dashed border-border p-3">
                  <span className="mt-0.5 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {KIND_LABEL[m.kind]}
                  </span>
                  <span className="flex-1 text-[12px] leading-relaxed">{m.text}</span>
                  <button
                    onClick={async () => {
                      await acceptMemory(m.id);
                      await refresh();
                    }}
                    className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-primary"
                    title="采纳"
                  >
                    <Check className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={async () => {
                      await deleteMemory(m.id);
                      await refresh();
                    }}
                    className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive"
                    title="忽略"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </>
          )}

          {tab === "missions" && (
            <>
              {!missions.length && (
                <div className="py-8 text-center text-[12px] text-muted-foreground">
                  还没有任务记录。运行智能问数或 Test Agent 后会自动留痕。
                </div>
              )}
              {missions.map((m) => (
                <div key={m.id} className="rounded-lg border border-border bg-card/40 p-3">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">{m.type}</span>
                    <span className="flex-1 truncate text-[12px] font-medium">{m.goal}</span>
                    <span className="text-[10px] text-muted-foreground">
                      {new Date(m.ts).toLocaleString("zh-CN")}
                    </span>
                    <button
                      onClick={async () => {
                        await deleteMission(m.id);
                        await refresh();
                      }}
                      className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  {!!m.steps?.length && (
                    <div className="mb-1 flex flex-wrap gap-1">
                      {m.steps.map((s, i) => (
                        <span key={i} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          {s.name}·{s.status}
                        </span>
                      ))}
                    </div>
                  )}
                  {m.sql?.[0] && (
                    <pre className="mb-1 overflow-x-auto rounded bg-muted/60 p-2 text-[10px] leading-relaxed">
                      {m.sql[0].slice(0, 400)}
                    </pre>
                  )}
                  {m.result && (
                    <p className="line-clamp-3 text-[11px] text-muted-foreground">{m.result}</p>
                  )}
                </div>
              ))}
              {missions.length > 0 && (
                <button
                  onClick={async () => {
                    if (!confirm("清空全部任务记忆？")) return;
                    await clearMissions();
                    await refresh();
                  }}
                  className="text-[11px] text-muted-foreground hover:text-destructive"
                >
                  清空任务记忆
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
