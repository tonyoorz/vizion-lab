// Long-term user memory: preferences, facts, glossary.
// Stored locally (IndexedDB), scored by keyword overlap + recency of use,
// serialized into the system prompt on every request.

import { idbClear, idbDelete, idbGetAll, idbPut } from "./db";

export type MemoryKind = "preference" | "fact" | "glossary";
export type MemoryStatus = "active" | "pending";

export interface UserMemory {
  id: string;
  kind: MemoryKind;
  text: string;
  source: "manual" | "auto";
  status: MemoryStatus;
  confidence: number;
  createdAt: number;
  lastUsedAt: number;
  hits: number;
}

export const KIND_LABEL: Record<MemoryKind, string> = {
  preference: "偏好",
  fact: "事实",
  glossary: "术语",
};

export const listMemories = () => idbGetAll<UserMemory>("user_memory");

export async function addMemory(
  text: string,
  opts: { kind?: MemoryKind; source?: "manual" | "auto"; status?: MemoryStatus; confidence?: number } = {},
): Promise<UserMemory | null> {
  const clean = text.trim();
  if (!clean) return null;
  const existing = await listMemories();
  const dup = existing.find((m) => m.text.trim() === clean);
  if (dup) return dup;
  const mem: UserMemory = {
    id: crypto.randomUUID(),
    kind: opts.kind ?? "preference",
    text: clean,
    source: opts.source ?? "manual",
    status: opts.status ?? "active",
    confidence: opts.confidence ?? 0.8,
    createdAt: Date.now(),
    lastUsedAt: 0,
    hits: 0,
  };
  await idbPut("user_memory", mem);
  return mem;
}

export const updateMemory = (mem: UserMemory) => idbPut("user_memory", mem);
export const deleteMemory = (id: string) => idbDelete("user_memory", id);
export const clearMemories = () => idbClear("user_memory");

export async function acceptMemory(id: string): Promise<void> {
  const all = await listMemories();
  const m = all.find((x) => x.id === id);
  if (!m) return;
  await idbPut("user_memory", { ...m, status: "active" as MemoryStatus });
}

// ---------- retrieval ----------

const tokenize = (s: string): string[] => {
  const lower = s.toLowerCase();
  const latin = lower.match(/[a-z0-9_]{2,}/g) ?? [];
  const cjk = lower.match(/[\u4e00-\u9fa5]/g) ?? [];
  const bigrams: string[] = [];
  for (let i = 0; i < cjk.length - 1; i++) bigrams.push(cjk[i] + cjk[i + 1]);
  return [...latin, ...bigrams];
};

/** Keyword overlap + recency/usage boost. Always keeps a few top memories. */
export function scoreMemories(memories: UserMemory[], query: string, topK = 8): UserMemory[] {
  const qTokens = new Set(tokenize(query));
  const now = Date.now();
  const scored = memories
    .filter((m) => m.status === "active")
    .map((m) => {
      const mt = tokenize(m.text);
      const overlap = mt.filter((t) => qTokens.has(t)).length;
      const rel = mt.length ? overlap / Math.sqrt(mt.length) : 0;
      const ageDays = (now - (m.lastUsedAt || m.createdAt)) / 86_400_000;
      const recency = 1 / (1 + ageDays / 14);
      const usage = Math.min(m.hits, 10) / 20;
      return { m, score: rel * 2 + recency * 0.6 + usage + m.confidence * 0.2 };
    })
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, topK).map((s) => s.m);
}

/** Mark memories as used (drives the recency/usage boost). */
export async function markUsed(memories: UserMemory[]): Promise<void> {
  const now = Date.now();
  for (const m of memories) {
    await idbPut("user_memory", { ...m, lastUsedAt: now, hits: m.hits + 1 });
  }
}

export function serializeMemories(memories: UserMemory[]): string {
  if (!memories.length) return "";
  const groups: Record<MemoryKind, string[]> = { preference: [], fact: [], glossary: [] };
  for (const m of memories) groups[m.kind].push(m.text);
  const parts: string[] = [];
  (Object.keys(groups) as MemoryKind[]).forEach((k) => {
    if (groups[k].length) parts.push(`【${KIND_LABEL[k]}】\n` + groups[k].map((t) => `- ${t}`).join("\n"));
  });
  return `以下是关于当前用户的长期记忆，回答时请自然地遵循（不要复述这些条目）：\n${parts.join("\n")}`;
}

/** One-call helper used by the chat/agent surfaces. */
export async function buildMemoryPrompt(query: string, topK = 8): Promise<string> {
  try {
    const all = await listMemories();
    const picked = scoreMemories(all, query, topK);
    if (!picked.length) return "";
    markUsed(picked).catch(() => {});
    return serializeMemories(picked);
  } catch {
    return "";
  }
}
