import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  Check,
  Copy,
  Database,
  Loader2,
  MessageSquarePlus,
  Mic,
  MoreHorizontal,
  Paperclip,
  Pencil,
  Pin,
  PinOff,
  RefreshCcw,
  Sparkles,
  Sparkle,
  Square,
  StopCircle,
  Trash2,
  User,
  X,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import MessageRenderer, { ToolResult } from "../chat/MessageRenderer";
import SlashMenu, { SLASH_COMMANDS, SlashCommand } from "../chat/SlashMenu";
import { segmentsToPlainText, parseAgentStream, extractToolCalls } from "../chat/agentParser";
import { duckdbManager, isDuckdbFile, TableInfo } from "@/lib/duckdb/client";
import { profileTable, riskScan, summarizeSchemaForPrompt } from "@/lib/duckdb/profile";
import { pyodideManager } from "@/lib/pyodide/client";


type Role = "user" | "assistant";
interface Attachment {
  id: string;
  name: string;
  kind: "image" | "file";
  dataUrl?: string; // for images, base64
  size: number;
  mime?: string;
  extractedText?: string;
  extracting?: boolean;
  pages?: number;
  truncated?: boolean;
  error?: string;
}
interface Msg {
  id: string;
  role: Role;
  content: string;
  attachments?: Attachment[];
  meta?: { ms?: number; chars?: number; model?: string };
  hidden?: boolean; // tool_result messages — sent to model, not shown
  toolResults?: Record<string, ToolResult>;
}
interface Conversation {
  id: string;
  title: string;
  pinned?: boolean;
  messages: Msg[];
  updatedAt: number;
}

const MODELS = [
  { id: "google/gemini-3-flash-preview", label: "Gemini 3 · Flash", hint: "默认 · 快速" },
  { id: "google/gemini-2.5-pro", label: "Gemini 2.5 · Pro", hint: "深度推理" },
  { id: "openai/gpt-5-mini", label: "GPT-5 · Mini", hint: "平衡" },
  { id: "openai/gpt-5", label: "GPT-5", hint: "最强" },
];

const STORAGE_KEY = "dtsv.chat.v2";

const IMAGE_QUICK_PROMPTS = [
  "这张图说明什么？",
  "找出异常点",
  "解读 KPI 变化趋势",
  "总结关键洞察（3 条以内）",
  "提取图中数据为表格",
];

const newConversation = (): Conversation => ({
  id: crypto.randomUUID(),
  title: "新对话",
  messages: [],
  updatedAt: Date.now(),
});

const newId = () => crypto.randomUUID();

interface Props {
  moduleKey?: string;
  moduleLabel?: string;
}

const AIChat = ({ moduleKey, moduleLabel }: Props) => {
  const [conversations, setConversations] = useState<Conversation[]>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Conversation[];
        if (Array.isArray(parsed) && parsed.length) return parsed;
      }
    } catch {}
    return [newConversation()];
  });
  const [activeId, setActiveId] = useState<string>(() => conversations[0].id);
  const [input, setInput] = useState("");
  const [model, setModel] = useState(MODELS[0].id);
  const [streaming, setStreaming] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashQuery, setSlashQuery] = useState("");
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const [menuId, setMenuId] = useState<string | null>(null);
  const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
  const [editingMsgVal, setEditingMsgVal] = useState("");
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [dbTables, setDbTables] = useState<TableInfo[]>(duckdbManager.listTables());
  const [dbLoading, setDbLoading] = useState(false);

  useEffect(() => {
    const unsub = duckdbManager.subscribe(() => setDbTables(duckdbManager.listTables()));
    return () => { unsub(); };
  }, []);

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const active = conversations.find((c) => c.id === activeId) ?? conversations[0];
  const isEmpty = active.messages.length === 0;

  // persist
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    } catch {}
  }, [conversations]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [active?.messages]);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  }, [input]);

  useEffect(() => {
    const close = () => setMenuId(null);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);

  const sortedConvos = useMemo(() => {
    return [...conversations].sort((a, b) => {
      if (!!b.pinned !== !!a.pinned) return (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
      return b.updatedAt - a.updatedAt;
    });
  }, [conversations]);

  const updateActive = (fn: (c: Conversation) => Conversation) =>
    setConversations((prev) => prev.map((c) => (c.id === activeId ? fn(c) : c)));

  const handleNew = () => {
    const c = newConversation();
    setConversations((prev) => [c, ...prev]);
    setActiveId(c.id);
    setInput("");
    setAttachments([]);
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  };

  // ----- file handling -----
  const extractFile = async (att: Attachment, file: File) => {
    try {
      const base64 = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => {
          const s = r.result as string;
          resolve(s.split(",")[1] ?? "");
        };
        r.onerror = reject;
        r.readAsDataURL(file);
      });
      const resp = await fetch(
        `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/extract-file`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
          },
          body: JSON.stringify({ name: file.name, mime: file.type, dataBase64: base64 }),
        },
      );
      const data = await resp.json();
      if (!resp.ok) throw new Error(data?.error || "解析失败");
      setAttachments((prev) =>
        prev.map((a) =>
          a.id === att.id
            ? {
                ...a,
                extracting: false,
                extractedText: data.text || "",
                pages: data.pages,
                truncated: !!data.truncated,
              }
            : a,
        ),
      );
    } catch (e: any) {
      setAttachments((prev) =>
        prev.map((a) =>
          a.id === att.id ? { ...a, extracting: false, error: e?.message || "解析失败" } : a,
        ),
      );
    }
  };

  const loadIntoDuckdb = async (file: File) => {
    setDbLoading(true);
    try {
      if (file.name.toLowerCase().endsWith(".duckdb")) {
        await duckdbManager.registerDuckdbFile(file);
      } else {
        await duckdbManager.registerTabular(file);
      }
    } catch (e: any) {
      alert(`加载 ${file.name} 失败: ${e?.message || e}`);
    } finally {
      setDbLoading(false);
    }
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files) return;
    const adds: { att: Attachment; file: File }[] = [];
    for (const f of Array.from(files).slice(0, 5)) {
      if (f.size > 100 * 1024 * 1024) continue;
      // Route data files to DuckDB instead of the extract-file edge function.
      if (isDuckdbFile(f)) {
        loadIntoDuckdb(f);
        continue;
      }
      if (f.size > 15 * 1024 * 1024) continue;
      const isImg = f.type.startsWith("image/");
      let dataUrl: string | undefined;
      if (isImg) {
        dataUrl = await new Promise<string>((resolve) => {
          const r = new FileReader();
          r.onload = () => resolve(r.result as string);
          r.readAsDataURL(f);
        });
      }
      const att: Attachment = {
        id: newId(),
        name: f.name,
        kind: isImg ? "image" : "file",
        dataUrl,
        size: f.size,
        mime: f.type,
        extracting: !isImg,
      };
      adds.push({ att, file: f });
    }
    setAttachments((prev) => [...prev, ...adds.map((x) => x.att)]);
    // kick off extraction in parallel for non-image files
    for (const { att, file } of adds) {
      if (att.kind === "file") extractFile(att, file);
    }
  };


  const onPaste = (e: React.ClipboardEvent) => {
    if (e.clipboardData.files?.length) {
      handleFiles(e.clipboardData.files);
    }
  };

  // ----- voice input -----
  const startRecording = async () => {
    if (recording || transcribing) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      const rec = new MediaRecorder(stream, { mimeType: mime });
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        if (blob.size < 600) return; // too short
        await transcribe(blob);
      };
      rec.start();
      recRef.current = rec;
      setRecording(true);
    } catch (e) {
      console.error("mic error", e);
      alert("无法访问麦克风，请检查权限。");
    }
  };

  const stopRecording = () => {
    recRef.current?.stop();
    recRef.current = null;
    setRecording(false);
  };

  const transcribe = async (blob: Blob) => {
    setTranscribing(true);
    try {
      const base64 = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => {
          const s = r.result as string;
          resolve(s.split(",")[1] ?? "");
        };
        r.onerror = reject;
        r.readAsDataURL(blob);
      });
      const resp = await fetch(
        `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/transcribe`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
          },
          body: JSON.stringify({ audio: base64, mime: "audio/webm" }),
        },
      );
      const data = await resp.json();
      if (!resp.ok) throw new Error(data?.error || "转写失败");
      const text = (data.text || "").trim();
      if (text) {
        setInput((prev) => (prev ? prev + (prev.endsWith(" ") ? "" : " ") + text : text));
        setTimeout(() => taRef.current?.focus(), 0);
      }
    } catch (e: any) {
      alert(e?.message || "转写失败");
    } finally {
      setTranscribing(false);
    }
  };


  // ----- slash menu -----
  const onInputChange = (val: string) => {
    setInput(val);
    const line = val.split("\n").pop() ?? "";
    if (line.startsWith("/")) {
      setSlashOpen(true);
      setSlashQuery(line.slice(1));
    } else {
      setSlashOpen(false);
    }
  };

  const pickSlash = (c: SlashCommand) => {
    setInput(c.prompt);
    setSlashOpen(false);
    setTimeout(() => taRef.current?.focus(), 0);
  };

  // ----- core send -----
  const buildGatewayMessages = (history: Msg[]) =>
    history.map((m) => {
      if (m.role !== "user" || !m.attachments?.length) {
        return { role: m.role, content: m.content };
      }
      // Build text prefix from extracted file contents
      const fileBlocks: string[] = [];
      for (const a of m.attachments) {
        if (a.kind === "file" && a.extractedText) {
          const header = `--- 附件: ${a.name}${a.pages ? ` (${a.pages} 页)` : ""}${a.truncated ? " · 已截断" : ""} ---`;
          fileBlocks.push(`${header}\n${a.extractedText}\n--- 附件结束 ---`);
        } else if (a.kind === "file" && a.error) {
          fileBlocks.push(`--- 附件: ${a.name} · 解析失败: ${a.error} ---`);
        }
      }
      const textBody = [fileBlocks.join("\n\n"), m.content].filter(Boolean).join("\n\n");
      const hasImage = m.attachments.some((a) => a.kind === "image" && a.dataUrl);
      if (!hasImage) {
        return { role: "user", content: textBody || "(附件)" };
      }
      const parts: any[] = [{ type: "text", text: textBody || "(图片)" }];
      for (const a of m.attachments) {
        if (a.kind === "image" && a.dataUrl) {
          parts.push({ type: "image_url", image_url: { url: a.dataUrl } });
        }
      }
      return { role: "user", content: parts };
    });

  // Truncate tool data before sending back to the model (keep token budget sane).
  const truncateResultForModel = (data: any): any => {
    if (data && Array.isArray(data.rows)) {
      const rowCount = data.rowCount ?? data.rows.length;
      return {
        columns: data.columns,
        rowCount,
        ms: data.ms,
        truncated: rowCount > 50,
        rows: data.rows.slice(0, 50),
      };
    }
    if (Array.isArray(data)) return data.slice(0, 50);
    return data;
  };

  const runStream = async (history: Msg[], assistantMsgId: string, round = 0) => {
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const startedAt = performance.now();
    const usedModel = model;
    const ctxParts: string[] = [];
    if (moduleLabel) {
      ctxParts.push(
        `User is currently viewing the "${moduleLabel}" module (key: ${moduleKey}). Reference this module in <cite> when relevant.`,
      );
    }
    const schema = summarizeSchemaForPrompt();
    if (schema) ctxParts.push(schema);
    const contextStr = ctxParts.join("\n\n") || undefined;

    try {
      const url = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/chat`;
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
        },
        body: JSON.stringify({
          messages: buildGatewayMessages(history),
          model,
          context: contextStr,
        }),
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        const err = await resp.json().catch(() => ({ error: "请求失败" }));
        updateActive((c) => ({
          ...c,
          messages: c.messages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: `⚠️ ${err.error || "请求失败，请稍后再试。"}` }
              : m,
          ),
        }));
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let acc = "";
      let done = false;

      while (!done) {
        const { done: d, value } = await reader.read();
        if (d) break;
        buffer += decoder.decode(value, { stream: true });
        let nl: number;
        while ((nl = buffer.indexOf("\n")) !== -1) {
          let line = buffer.slice(0, nl);
          buffer = buffer.slice(nl + 1);
          if (line.endsWith("\r")) line = line.slice(0, -1);
          if (!line.startsWith("data: ")) continue;
          const json = line.slice(6).trim();
          if (json === "[DONE]") {
            done = true;
            break;
          }
          try {
            const parsed = JSON.parse(json);
            const chunk = parsed.choices?.[0]?.delta?.content as string | undefined;
            if (chunk) {
              acc += chunk;
              updateActive((c) => ({
                ...c,
                messages: c.messages.map((m) =>
                  m.id === assistantMsgId ? { ...m, content: acc } : m,
                ),
                updatedAt: Date.now(),
              }));
            }
          } catch {
            buffer = line + "\n" + buffer;
            break;
          }
        }
      }
    } catch (e: any) {
      if (e.name !== "AbortError") {
        updateActive((c) => ({
          ...c,
          messages: c.messages.map((m) =>
            m.id === assistantMsgId ? { ...m, content: "⚠️ 连接中断，请重试。" } : m,
          ),
        }));
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
      const ms = Math.round(performance.now() - startedAt);
      let finalContent = "";
      updateActive((c) => {
        const msgs = c.messages.map((m) => {
          if (m.id === assistantMsgId) {
            finalContent = m.content || "";
            return { ...m, meta: { ms, chars: finalContent.length, model: usedModel } };
          }
          return m;
        });
        return { ...c, messages: msgs };
      });

      // ----- Tool execution loop (DuckDB) -----
      const toolCalls = extractToolCalls(finalContent);
      if (toolCalls.length && round < 4 && !controller.signal.aborted) {
        const results: Record<string, ToolResult> = {};
        for (const tc of toolCalls) {
          results[tc.toolId] = await executeTool(tc.toolName, tc.text, tc.args);
        }
        // Attach results to the assistant message for UI rendering
        updateActive((c) => ({
          ...c,
          messages: c.messages.map((m) =>
            m.id === assistantMsgId ? { ...m, toolResults: { ...(m.toolResults || {}), ...results } } : m,
          ),
        }));
        // Build hidden tool_result message for next model turn
        const toolResultText = toolCalls
          .map((tc) => {
            const r = results[tc.toolId];
            const payload = r.ok
              ? JSON.stringify(truncateResultForModel(r.data))
              : JSON.stringify({ error: r.error });
            return `<tool_result id="${tc.toolId}" name="${tc.toolName}" ok="${r.ok}">${payload}</tool_result>`;
          })
          .join("\n");
        const hiddenMsg: Msg = {
          id: newId(),
          role: "user",
          content: toolResultText,
          hidden: true,
        };
        const nextAsst: Msg = { id: newId(), role: "assistant", content: "" };
        updateActive((c) => ({
          ...c,
          messages: [...c.messages, hiddenMsg, nextAsst],
          updatedAt: Date.now(),
        }));
        const nextHistory = [...history, { ...(active.messages.find((m) => m.id === assistantMsgId)!), content: finalContent }, hiddenMsg];
        await runStream(nextHistory, nextAsst.id, round + 1);
        return;
      }

      // Auto-generate a concise title after first round
      const conv = conversations.find((c) => c.id === activeId);
      const isFirstRound =
        conv &&
        conv.messages.filter((m) => m.role === "assistant").length <= 1 &&
        (conv.title === "新对话" || conv.title.length <= 28);
      if (isFirstRound && finalContent.trim()) {
        const firstUser = history.find((m) => m.role === "user")?.content || "";
        generateTitle(firstUser, finalContent).catch(() => {});
      }
    }
  };

  const executeTool = async (name: string, payload: string, args?: Record<string, string>): Promise<ToolResult> => {
    const start = performance.now();
    try {
      if (name === "list_tables") {
        return { ok: true, ms: Math.round(performance.now() - start), data: duckdbManager.listTables() };
      }
      if (name === "query_sql") {
        const sql = payload.trim();
        const res = await duckdbManager.runQuery(sql, 200);
        return { ok: true, ms: res.ms, data: res };
      }
      if (name === "profile_table") {
        const t = (args?.table || payload).trim();
        const res = await profileTable(t);
        return { ok: true, ms: Math.round(performance.now() - start), data: res };
      }
      if (name === "risk_scan") {
        const t = (args?.table || payload).trim() || undefined;
        const res = await riskScan(t);
        return { ok: true, ms: Math.round(performance.now() - start), data: res };
      }
      return { ok: false, error: `未知工具: ${name}` };
    } catch (e: any) {
      return { ok: false, error: e?.message || String(e), ms: Math.round(performance.now() - start) };
    }
  };


  const generateTitle = async (userQ: string, asstA: string) => {
    try {
      const url = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/chat`;
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
        },
        body: JSON.stringify({
          model: "google/gemini-3-flash-preview",
          messages: [
            {
              role: "user",
              content: `请用 6-12 个汉字概括下面对话的主题，只输出标题，不要标点、引号、前缀。\n\n用户：${userQ.slice(0, 200)}\n助手：${asstA.replace(/<[^>]+>/g, " ").slice(0, 400)}`,
            },
          ],
        }),
      });
      if (!resp.ok || !resp.body) return;
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let title = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl: number;
        while ((nl = buf.indexOf("\n")) !== -1) {
          const line = buf.slice(0, nl).replace(/\r$/, "");
          buf = buf.slice(nl + 1);
          if (!line.startsWith("data: ")) continue;
          const json = line.slice(6).trim();
          if (json === "[DONE]") break;
          try {
            const p = JSON.parse(json);
            title += p.choices?.[0]?.delta?.content ?? "";
          } catch {}
        }
      }
      title = title.replace(/["'""''`《》]/g, "").replace(/\s+/g, "").trim().slice(0, 16);
      if (title) {
        setConversations((prev) =>
          prev.map((c) => (c.id === activeId ? { ...c, title } : c)),
        );
      }
    } catch {}
  };


  const send = async (text?: string) => {
    const content = (text ?? input).trim();
    if ((!content && attachments.length === 0) || streaming) return;
    if (attachments.some((a) => a.extracting)) return;

    const userMsg: Msg = {
      id: newId(),
      role: "user",
      content,
      attachments: attachments.length ? attachments : undefined,
    };
    const assistantMsg: Msg = { id: newId(), role: "assistant", content: "" };
    const history = [...active.messages, userMsg];
    updateActive((c) => ({
      ...c,
      title: c.messages.length === 0 && content ? content.slice(0, 28) : c.title,
      messages: [...history, assistantMsg],
      updatedAt: Date.now(),
    }));
    setInput("");
    setAttachments([]);
    setSlashOpen(false);
    await runStream(history, assistantMsg.id);
  };

  const regenerate = async () => {
    if (streaming) return;
    const msgs = active.messages;
    // find last assistant and drop it
    let lastAsst = -1;
    for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i].role === "assistant") { lastAsst = i; break; }
    if (lastAsst < 0) return;
    const trimmed = msgs.slice(0, lastAsst);
    const assistantMsg: Msg = { id: newId(), role: "assistant", content: "" };
    updateActive((c) => ({ ...c, messages: [...trimmed, assistantMsg], updatedAt: Date.now() }));
    await runStream(trimmed, assistantMsg.id);
  };

  const editUserMessage = async (msgId: string) => {
    const idx = active.messages.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    const trimmed = active.messages.slice(0, idx);
    const newUser: Msg = { ...active.messages[idx], content: editingMsgVal };
    const assistantMsg: Msg = { id: newId(), role: "assistant", content: "" };
    const history = [...trimmed, newUser];
    updateActive((c) => ({ ...c, messages: [...history, assistantMsg], updatedAt: Date.now() }));
    setEditingMsgId(null);
    setEditingMsgVal("");
    await runStream(history, assistantMsg.id);
  };

  // ----- conversation management -----
  const renameConvo = (id: string, title: string) =>
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title: title || "新对话" } : c)));

  const deleteConvo = (id: string) => {
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      if (next.length === 0) {
        const n = newConversation();
        setActiveId(n.id);
        return [n];
      }
      if (id === activeId) setActiveId(next[0].id);
      return next;
    });
  };

  const togglePin = (id: string) =>
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, pinned: !c.pinned } : c)));

  // ----- UI helpers -----
  const lastSeg = (m: Msg) => parseAgentStream(m.content);

  return (
    <div className="-m-6 flex h-[calc(100vh-64px)] overflow-hidden">
      {/* Conversation list */}
      <aside className="hidden w-[260px] flex-col border-r border-border bg-muted/30 lg:flex">
        <div className="p-3">
          <button
            onClick={handleNew}
            className="flex w-full items-center justify-between rounded-lg border border-border bg-card px-3 py-2.5 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-secondary"
          >
            <span className="flex items-center gap-2">
              <MessageSquarePlus className="h-4 w-4" /> 新对话
            </span>
            <kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
              ⌘N
            </kbd>
          </button>
        </div>
        <div className="px-3 pb-2">
          <p className="filter-label px-1">会话</p>
        </div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {sortedConvos.map((c) => (
            <div
              key={c.id}
              className={`group relative flex items-center gap-1 rounded-md px-1 ${
                c.id === activeId ? "bg-primary/10" : "hover:bg-muted"
              }`}
            >
              {renameId === c.id ? (
                <input
                  autoFocus
                  value={renameVal}
                  onChange={(e) => setRenameVal(e.target.value)}
                  onBlur={() => {
                    renameConvo(c.id, renameVal.trim());
                    setRenameId(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      renameConvo(c.id, renameVal.trim());
                      setRenameId(null);
                    }
                    if (e.key === "Escape") setRenameId(null);
                  }}
                  className="my-1 w-full rounded bg-background px-1.5 py-1 text-sm outline-none ring-1 ring-primary/40"
                />
              ) : (
                <button
                  onClick={() => setActiveId(c.id)}
                  className={`flex flex-1 items-center gap-1.5 truncate py-2 pl-1.5 text-left text-sm transition-colors ${
                    c.id === activeId ? "text-primary" : "text-muted-foreground group-hover:text-foreground"
                  }`}
                >
                  {c.pinned && <Pin className="h-3 w-3 shrink-0" />}
                  <span className="truncate">{c.title || "新对话"}</span>
                </button>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setMenuId(menuId === c.id ? null : c.id);
                }}
                className="mr-1 hidden h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-background hover:text-foreground group-hover:flex"
              >
                <MoreHorizontal className="h-3.5 w-3.5" />
              </button>
              {menuId === c.id && (
                <div
                  onClick={(e) => e.stopPropagation()}
                  className="absolute right-1 top-9 z-20 w-36 overflow-hidden rounded-lg border border-border bg-popover py-1 shadow-xl"
                >
                  <MenuItem
                    icon={c.pinned ? PinOff : Pin}
                    onClick={() => {
                      togglePin(c.id);
                      setMenuId(null);
                    }}
                  >
                    {c.pinned ? "取消置顶" : "置顶"}
                  </MenuItem>
                  <MenuItem
                    icon={Pencil}
                    onClick={() => {
                      setRenameId(c.id);
                      setRenameVal(c.title);
                      setMenuId(null);
                    }}
                  >
                    重命名
                  </MenuItem>
                  <MenuItem
                    icon={Trash2}
                    destructive
                    onClick={() => {
                      deleteConvo(c.id);
                      setMenuId(null);
                    }}
                  >
                    删除
                  </MenuItem>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-muted-foreground">
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Sparkles className="h-3.5 w-3.5" />
            </div>
            <span className="leading-tight">
              由 Lovable AI 提供
              <br />
              <span className="text-[10px]">本地保存 · 实时流式</span>
            </span>
          </div>
        </div>
      </aside>

      {/* Main chat area */}
      <div className="flex flex-1 flex-col bg-background">
        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-border px-6 py-3">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10">
              <Sparkles className="h-3.5 w-3.5 text-primary" />
            </div>
            <div>
              <p className="text-sm font-semibold leading-tight text-foreground">
                DTSV Intelligence
              </p>
              <p className="text-[11px] text-muted-foreground">
                数据分析智能体 · 可视化推理与工具调用
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {(dbTables.length > 0 || dbLoading) && (
              <div className="flex items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1 text-[11px]">
                {dbLoading ? (
                  <Loader2 className="h-3 w-3 animate-spin text-primary" />
                ) : (
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  </span>
                )}
                <Database className="h-3 w-3 text-muted-foreground" />
                <span className="font-medium text-foreground">DuckDB</span>
                <span className="text-muted-foreground">
                  {dbTables.length} 表 · {dbTables.reduce((s, t) => s + t.rows, 0).toLocaleString()} 行
                </span>
                <button
                  onClick={() => {
                    if (confirm("断开本地 DuckDB 数据？")) duckdbManager.reset();
                  }}
                  className="ml-1 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                  title="断开"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )}
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-foreground outline-none transition-colors hover:bg-secondary focus:border-primary"
            >
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} · {m.hint}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {isEmpty ? (
            <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-6">
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="mb-8 text-center"
              >
                <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-primary to-primary/60 shadow-lg shadow-primary/20">
                  <Sparkles className="h-5 w-5 text-primary-foreground" />
                </div>
                <h2 className="text-2xl font-bold tracking-tight text-foreground">
                  你好，今天要分析什么？
                </h2>
                <p className="mt-1.5 text-sm text-muted-foreground">
                  输入 <kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">/</kbd> 调用预设命令，或直接提问。
                </p>
              </motion.div>
              <div className="grid w-full gap-2 sm:grid-cols-2">
                {SLASH_COMMANDS.slice(0, 4).map((s, i) => (
                  <motion.button
                    key={s.id}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.05 * i }}
                    onClick={() => send(s.prompt)}
                    className="group rounded-xl border border-border bg-card p-3.5 text-left transition-all hover:border-primary/40 hover:shadow-sm"
                  >
                    <div className="mb-1.5 flex h-7 w-7 items-center justify-center rounded-md bg-muted text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
                      <s.icon className="h-3.5 w-3.5" />
                    </div>
                    <p className="text-sm font-medium text-foreground">{s.label.replace("/", "")}</p>
                    <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                      {s.prompt}
                    </p>
                  </motion.button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
              <AnimatePresence initial={false}>
                {active.messages.filter((m) => !m.hidden).map((m, i, arr) => {
                  const isLastAsst =
                    m.role === "assistant" && i === arr.length - 1;
                  return (
                    <motion.div
                      key={m.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="group flex gap-3"
                    >
                      <div
                        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
                          m.role === "user"
                            ? "bg-secondary text-foreground"
                            : "bg-gradient-to-br from-primary to-primary/60 text-primary-foreground"
                        }`}
                      >
                        {m.role === "user" ? <User className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
                      </div>
                      <div className="min-w-0 flex-1 pt-0.5">
                        <p className="mb-1 text-xs font-medium text-muted-foreground">
                          {m.role === "user" ? "你" : "DTSV Intelligence"}
                        </p>

                        {/* attachments */}
                        {m.attachments && m.attachments.length > 0 && (
                          <div className="mb-2 flex flex-wrap gap-2">
                            {m.attachments.map((a) =>
                              a.kind === "image" && a.dataUrl ? (
                                <img
                                  key={a.id}
                                  src={a.dataUrl}
                                  alt={a.name}
                                  className="max-h-40 rounded-lg border border-border"
                                />
                              ) : (
                                <div
                                  key={a.id}
                                  className="flex items-center gap-1.5 rounded-md border border-border bg-muted px-2 py-1 text-xs text-muted-foreground"
                                >
                                  <Paperclip className="h-3 w-3" />
                                  {a.name}
                                </div>
                              ),
                            )}
                          </div>
                        )}

                        {/* body */}
                        {editingMsgId === m.id ? (
                          <div className="space-y-2">
                            <textarea
                              value={editingMsgVal}
                              onChange={(e) => setEditingMsgVal(e.target.value)}
                              rows={3}
                              className="w-full resize-none rounded-lg border border-primary/40 bg-card p-2.5 text-sm outline-none"
                            />
                            <div className="flex gap-2">
                              <button
                                onClick={() => editUserMessage(m.id)}
                                className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                              >
                                重发
                              </button>
                              <button
                                onClick={() => setEditingMsgId(null)}
                                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
                              >
                                取消
                              </button>
                            </div>
                          </div>
                        ) : m.role === "assistant" && m.content === "" && streaming && isLastAsst ? (
                          <TypingDots />
                        ) : m.role === "assistant" ? (
                          <>
                            <MessageRenderer
                              content={m.content}
                              streaming={streaming && isLastAsst}
                              onPickFollowup={isLastAsst && !streaming ? (q) => send(q) : undefined}
                              toolResults={m.toolResults}
                            />
                            {m.meta && !(streaming && isLastAsst) && (
                              <MessageStats meta={m.meta} />
                            )}
                          </>

                        ) : (
                          <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                            {m.content}
                          </p>
                        )}

                        {/* actions */}
                        {!editingMsgId && (
                          <div className="mt-1.5 flex h-6 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                            {m.role === "assistant" ? (
                              <>
                                <CopyBtn
                                  text={segmentsToPlainText(lastSeg(m)) || m.content}
                                />
                                {isLastAsst && !streaming && (
                                  <ActionBtn icon={RefreshCcw} label="重新生成" onClick={regenerate} />
                                )}
                              </>
                            ) : (
                              <>
                                <CopyBtn text={m.content} />
                                <ActionBtn
                                  icon={Pencil}
                                  label="编辑"
                                  onClick={() => {
                                    setEditingMsgId(m.id);
                                    setEditingMsgVal(m.content);
                                  }}
                                />
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-border bg-background px-6 py-4">
          <div className="mx-auto max-w-3xl">
            {/* context chip */}
            {moduleLabel && (
              <div className="mb-2 flex items-center gap-1.5 text-[11px]">
                <span className="inline-flex items-center gap-1 rounded-md border border-border bg-muted px-1.5 py-0.5 text-muted-foreground">
                  <Sparkle className="h-2.5 w-2.5 text-primary" />
                  上下文：<span className="font-medium text-foreground">{moduleLabel}</span>
                </span>
              </div>
            )}

            {/* attachment preview */}
            {attachments.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {attachments.map((a) => (
                  <div
                    key={a.id}
                    title={
                      a.error
                        ? `解析失败: ${a.error}`
                        : a.extractedText
                          ? `已解析 ${a.extractedText.length.toLocaleString()} 字符${a.truncated ? "（已截断）" : ""}`
                          : a.extracting
                            ? "解析中…"
                            : a.name
                    }
                    className={`group/att relative flex items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-xs text-foreground ${
                      a.error
                        ? "border-destructive/40"
                        : a.extracting
                          ? "border-primary/40"
                          : "border-border"
                    }`}
                  >
                    {a.kind === "image" && a.dataUrl ? (
                      <img src={a.dataUrl} className="h-6 w-6 rounded object-cover" alt="" />
                    ) : a.extracting ? (
                      <Loader2 className="h-3 w-3 animate-spin text-primary" />
                    ) : a.error ? (
                      <X className="h-3 w-3 text-destructive" />
                    ) : a.extractedText ? (
                      <Check className="h-3 w-3 text-primary" />
                    ) : (
                      <Paperclip className="h-3 w-3 text-muted-foreground" />
                    )}
                    <span className="max-w-[140px] truncate">{a.name}</span>
                    {a.kind === "file" && a.pages != null && !a.error && (
                      <span className="text-[10px] text-muted-foreground">· {a.pages}p</span>
                    )}
                    <button
                      onClick={() => setAttachments((p) => p.filter((x) => x.id !== a.id))}
                      className="ml-0.5 rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* image quick-prompts */}
            {attachments.some((a) => a.kind === "image") && !streaming && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <span className="inline-flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  <Sparkle className="h-2.5 w-2.5 text-primary" /> 图片快捷提问
                </span>
                {IMAGE_QUICK_PROMPTS.map((q) => (
                  <button
                    key={q}
                    onClick={() => send(q)}
                    className="rounded-full border border-border bg-card px-2.5 py-1 text-[11px] text-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-primary"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}

            <div className="relative flex items-end gap-2 rounded-2xl border border-border bg-card px-3 py-2 shadow-sm transition-colors focus-within:border-primary/50 focus-within:shadow-md">
              {slashOpen && (
                <SlashMenu
                  query={slashQuery}
                  onPick={pickSlash}
                  onClose={() => setSlashOpen(false)}
                />
              )}
              <input
                ref={fileRef}
                type="file"
                multiple
                accept="image/*,.pdf,.pptx,.docx,.xlsx,.csv,.tsv,.parquet,.duckdb,.txt,.json,.ndjson,.md"
                hidden
                onChange={(e) => {
                  handleFiles(e.target.files);
                  e.currentTarget.value = "";
                }}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="mb-1 flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title="附件 (图片 / 文件)"
              >
                <Paperclip className="h-4 w-4" />
              </button>
              <textarea
                ref={taRef}
                value={input}
                onChange={(e) => onInputChange(e.target.value)}
                onPaste={onPaste}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !slashOpen) {
                    e.preventDefault();
                    send();
                  }
                  if (e.key === "Escape") setSlashOpen(false);
                }}
                rows={1}
                placeholder="提问数据、要求总结，或输入 / 调用命令…  (Shift + Enter 换行)"
                className="max-h-[200px] min-h-[28px] flex-1 resize-none bg-transparent py-1.5 text-sm leading-relaxed text-foreground outline-none placeholder:text-muted-foreground"
              />
              <button
                type="button"
                onClick={recording ? stopRecording : startRecording}
                disabled={transcribing}
                className={`mb-1 flex h-8 w-8 items-center justify-center rounded-lg transition-colors ${
                  recording
                    ? "bg-destructive text-destructive-foreground hover:bg-destructive/90"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                } disabled:opacity-50`}
                title={recording ? "停止录音" : transcribing ? "转写中…" : "语音输入"}
              >
                {transcribing ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : recording ? (
                  <Square className="h-3.5 w-3.5 fill-current" />
                ) : (
                  <Mic className="h-4 w-4" />
                )}
              </button>
              {streaming ? (
                <button
                  onClick={stop}
                  className="mb-1 flex h-8 w-8 items-center justify-center rounded-lg bg-foreground text-background transition-opacity hover:opacity-90"
                  title="停止生成"
                >
                  <StopCircle className="h-4 w-4" />
                </button>
              ) : (
                <button
                  onClick={() => send()}
                  disabled={
                    (!input.trim() && attachments.length === 0) ||
                    attachments.some((a) => a.extracting)
                  }
                  className="mb-1 flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-40"
                >
                  <ArrowUp className="h-4 w-4" />
                </button>
              )}
            </div>
            <p className="mt-2 text-center text-[11px] text-muted-foreground">
              DTSV Intelligence 可能出错，请核对关键数据。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

// ----- small sub-components -----

const TypingDots = () => (
  <div className="flex h-6 items-center gap-1">
    {[0, 1, 2].map((i) => (
      <motion.span
        key={i}
        className="h-1.5 w-1.5 rounded-full bg-muted-foreground"
        animate={{ opacity: [0.2, 1, 0.2] }}
        transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.18 }}
      />
    ))}
  </div>
);

const MessageStats = ({
  meta,
}: {
  meta: { ms?: number; chars?: number; model?: string };
}) => {
  const modelLabel = MODELS.find((m) => m.id === meta.model)?.label ?? meta.model;
  const seconds = meta.ms != null ? (meta.ms / 1000).toFixed(1) + "s" : null;
  const tokens = meta.chars != null ? Math.max(1, Math.round(meta.chars / 3.5)) : null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] text-muted-foreground">
      {modelLabel && (
        <span className="inline-flex items-center gap-1">
          <span className="h-1 w-1 rounded-full bg-primary/60" />
          {modelLabel}
        </span>
      )}
      {seconds && <span>· {seconds}</span>}
      {tokens && <span>· ~{tokens.toLocaleString()} tok</span>}
    </div>
  );
};

const ActionBtn = ({
  icon: Icon,
  label,
  onClick,
}: {
  icon: any;
  label: string;
  onClick: () => void;
}) => (
  <button
    onClick={onClick}
    title={label}
    className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
  >
    <Icon className="h-3.5 w-3.5" />
  </button>
);

const CopyBtn = ({ text }: { text: string }) => {
  const [done, setDone] = useState(false);
  return (
    <ActionBtn
      icon={done ? Check : Copy}
      label={done ? "已复制" : "复制"}
      onClick={() => {
        navigator.clipboard.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1200);
      }}
    />
  );
};

const MenuItem = ({
  icon: Icon,
  children,
  onClick,
  destructive,
}: {
  icon: any;
  children: any;
  onClick: () => void;
  destructive?: boolean;
}) => (
  <button
    onClick={onClick}
    className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-accent ${
      destructive ? "text-destructive" : "text-foreground"
    }`}
  >
    <Icon className="h-3.5 w-3.5" />
    {children}
  </button>
);

export default AIChat;
