// Session memory: conversations + messages persisted in IndexedDB,
// with one-time migration from the legacy localStorage payload.

import { idbBulkPut, idbDelete, idbDeleteByIndex, idbGetAll, idbGetByIndex, idbPut } from "./db";

export interface StoredConversationMeta {
  id: string;
  title: string;
  pinned?: boolean;
  updatedAt: number;
  /** Rolling summary of older turns, injected instead of raw history. */
  summary?: string;
  /** How many messages the summary already covers. */
  summarizedCount?: number;
}

export interface StoredMessage {
  id: string;
  conversationId: string;
  order: number;
  payload: unknown; // the app-level Msg object
}

const LEGACY_KEY = "dtsv.chat.v2";
const MIGRATED_FLAG = "dtsv.memory.migrated.v1";

export interface AnyConversation {
  id: string;
  title: string;
  pinned?: boolean;
  updatedAt: number;
  messages: any[];
  summary?: string;
  summarizedCount?: number;
}

/** Load every conversation (meta + messages) ordered by recency. */
export async function loadConversations(): Promise<AnyConversation[]> {
  await migrateFromLocalStorage();
  const metas = await idbGetAll<StoredConversationMeta>("conversations");
  const all = await idbGetAll<StoredMessage>("messages");
  const byConv = new Map<string, StoredMessage[]>();
  for (const m of all) {
    const arr = byConv.get(m.conversationId) ?? [];
    arr.push(m);
    byConv.set(m.conversationId, arr);
  }
  return metas
    .map((meta) => ({
      ...meta,
      messages: (byConv.get(meta.id) ?? []).sort((a, b) => a.order - b.order).map((m) => m.payload),
    }))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export async function loadMessages(conversationId: string): Promise<any[]> {
  const rows = await idbGetByIndex<StoredMessage>("messages", "conversationId", conversationId);
  return rows.sort((a, b) => a.order - b.order).map((r) => r.payload);
}

/** Replace a conversation's stored state (meta + full message list). */
export async function persistConversation(conv: AnyConversation): Promise<void> {
  const meta: StoredConversationMeta = {
    id: conv.id,
    title: conv.title,
    pinned: conv.pinned,
    updatedAt: conv.updatedAt,
    summary: conv.summary,
    summarizedCount: conv.summarizedCount,
  };
  await idbPut("conversations", meta);
  await idbDeleteByIndex("messages", "conversationId", conv.id);
  await idbBulkPut<StoredMessage>(
    "messages",
    conv.messages.map((payload: any, order: number) => ({
      id: `${conv.id}:${payload?.id ?? order}`,
      conversationId: conv.id,
      order,
      payload,
    })),
  );
}

export async function deleteConversation(id: string): Promise<void> {
  await idbDelete("conversations", id);
  await idbDeleteByIndex("messages", "conversationId", id);
}

async function migrateFromLocalStorage(): Promise<void> {
  try {
    if (localStorage.getItem(MIGRATED_FLAG)) return;
    const raw = localStorage.getItem(LEGACY_KEY);
    localStorage.setItem(MIGRATED_FLAG, "1");
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || !parsed.length) return;
    for (const c of parsed) {
      if (!c?.id) continue;
      await persistConversation({
        id: c.id,
        title: c.title ?? "新对话",
        pinned: c.pinned,
        updatedAt: c.updatedAt ?? Date.now(),
        messages: Array.isArray(c.messages) ? c.messages : [],
      });
    }
    localStorage.removeItem(LEGACY_KEY);
  } catch (e) {
    console.warn("[memory] migration skipped", e);
  }
}

// ---------- rolling summary ----------

/** Older turns beyond this count get compressed into a rolling summary. */
export const SUMMARY_THRESHOLD = 24;
export const SUMMARY_KEEP_RECENT = 12;

const CHAT_URL = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/chat`;
const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
});

/** Ask the model for a compact Chinese summary of the given turns. */
export async function summarizeTurns(turns: { role: string; content: string }[], previous?: string): Promise<string> {
  const transcript = turns
    .filter((t) => t.content?.trim())
    .map((t) => `${t.role === "user" ? "用户" : "助手"}：${t.content.slice(0, 800)}`)
    .join("\n");
  const resp = await fetch(CHAT_URL, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      model: "google/gemini-3-flash-preview",
      messages: [
        {
          role: "user",
          content:
            `把下面的对话压缩成不超过 200 字的中文摘要，保留：分析目标、涉及的表/字段/口径、已得出的结论与数字、未完成的事项。只输出摘要正文。\n` +
            (previous ? `\n【已有摘要】\n${previous}\n` : "") +
            `\n【新增对话】\n${transcript}`,
        },
      ],
    }),
  });
  if (!resp.ok) throw new Error("summary failed");
  const text = await readStreamText(resp);
  return text.trim();
}

/** The chat edge function streams SSE; collect it into a plain string. */
export async function readStreamText(resp: Response): Promise<string> {
  if (!resp.body) return "";
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let out = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const data = trimmed.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      try {
        const json = JSON.parse(data);
        out += json?.choices?.[0]?.delta?.content ?? "";
      } catch {
        /* ignore partial frames */
      }
    }
  }
  return out;
}
