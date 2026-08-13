// Auto-extract candidate long-term memories from a finished turn.
// Candidates land as `pending` and only take effect after the user accepts them
// in the memory panel — this keeps the memory store from being polluted.

import { readStreamText } from "./conversations";
import { addMemory, listMemories, type MemoryKind } from "./user-memory";

const CHAT_URL = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/chat`;

interface Candidate {
  kind: MemoryKind;
  text: string;
  confidence?: number;
}

const PROMPT = `你是记忆抽取器。从下面一轮对话中抽取「值得长期记住」的用户信息，只抽取稳定的、以后仍然适用的内容：
- preference：用户偏好（默认时间窗、常看模块、图表偏好、回答风格、口径习惯）
- fact：关于用户/团队/项目的事实（负责的模块、所在团队、常用数据源）
- glossary：用户特有的术语或口径定义

不要抽取：一次性的问题、临时数字、模型自己的结论、寒暄。
严格只输出 JSON 数组，形如 [{"kind":"preference","text":"...","confidence":0.8}]，没有可抽取内容时输出 []。`;

export async function extractMemories(userText: string, assistantText: string): Promise<number> {
  const convo = `用户：${userText.slice(0, 1500)}\n助手：${assistantText.slice(0, 1500)}`;
  let raw = "";
  try {
    const resp = await fetch(CHAT_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
      },
      body: JSON.stringify({
        model: "google/gemini-3-flash-preview",
        messages: [{ role: "user", content: `${PROMPT}\n\n【对话】\n${convo}` }],
      }),
    });
    if (!resp.ok) return 0;
    raw = await readStreamText(resp);
  } catch {
    return 0;
  }

  const match = raw.match(/\[[\s\S]*\]/);
  if (!match) return 0;
  let candidates: Candidate[] = [];
  try {
    candidates = JSON.parse(match[0]);
  } catch {
    return 0;
  }
  if (!Array.isArray(candidates) || !candidates.length) return 0;

  const existing = await listMemories();
  const seen = new Set(existing.map((m) => m.text.trim()));
  let added = 0;
  for (const c of candidates.slice(0, 5)) {
    const text = (c?.text ?? "").trim();
    if (!text || text.length > 200 || seen.has(text)) continue;
    const kind: MemoryKind = ["preference", "fact", "glossary"].includes(c.kind) ? c.kind : "preference";
    await addMemory(text, { kind, source: "auto", status: "pending", confidence: c.confidence ?? 0.6 });
    seen.add(text);
    added++;
  }
  return added;
}
