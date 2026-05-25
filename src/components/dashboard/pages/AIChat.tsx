import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  Check,
  Copy,
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
  Keyboard,
  PanelRight,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import MessageRenderer from "../chat/MessageRenderer";
import MessageFeedback from "../chat/MessageFeedback";
import SlashMenu, { SLASH_COMMANDS, SlashCommand } from "../chat/SlashMenu";
import EmptyState from "../chat/EmptyState";
import ContextPanel from "../chat/ContextPanel";
import { segmentsToPlainText, parseAgentStream } from "../chat/agentParser";

type Role = "user" | "assistant";
interface Attachment {
  id: string;
  name: string;
  kind: "image" | "file";
  dataUrl?: string;
  size: number;
}
interface Msg {
  id: string;
  role: Role;
  content: string;
  attachments?: Attachment[];
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

const KEYBOARD_SHORTCUTS = [
  { key: "⌘ N", action: "新对话" },
  { key: "⌘ K", action: "聚焦输入" },
  { key: "Esc", action: "停止生成" },
  { key: "/", action: "命令菜单" },
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
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

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

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Cmd/Ctrl + N: New conversation
      if ((e.metaKey || e.ctrlKey) && e.key === "n") {
        e.preventDefault();
        handleNew();
      }
      // Cmd/Ctrl + K: Focus input
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        taRef.current?.focus();
      }
      // Escape: Stop streaming
      if (e.key === "Escape" && streaming) {
        stop();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [streaming]);

  const sortedConvos = useMemo(() => {
    return [...conversations].sort((a, b) => {
      if (!!b.pinned !== !!a.pinned) return (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
      return b.updatedAt - a.updatedAt;
    });
  }, [conversations]);

  const recentConversations = useMemo(() => {
    return conversations
      .filter((c) => c.messages.length > 0 && c.id !== activeId)
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, 5)
      .map((c) => ({ id: c.id, title: c.title }));
  }, [conversations, activeId]);

  const updateActive = (fn: (c: Conversation) => Conversation) =>
    setConversations((prev) => prev.map((c) => (c.id === activeId ? fn(c) : c)));

  const handleNew = useCallback(() => {
    const c = newConversation();
    setConversations((prev) => [c, ...prev]);
    setActiveId(c.id);
    setInput("");
    setAttachments([]);
  }, []);

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  };

  // ----- file handling -----
  const handleFiles = async (files: FileList | null) => {
    if (!files) return;
    const adds: Attachment[] = [];
    for (const f of Array.from(files).slice(0, 5)) {
      if (f.size > 8 * 1024 * 1024) continue;
      const isImg = f.type.startsWith("image/");
      let dataUrl: string | undefined;
      if (isImg) {
        dataUrl = await new Promise<string>((resolve) => {
          const r = new FileReader();
          r.onload = () => resolve(r.result as string);
          r.readAsDataURL(f);
        });
      }
      adds.push({
        id: newId(),
        name: f.name,
        kind: isImg ? "image" : "file",
        dataUrl,
        size: f.size,
      });
    }
    setAttachments((prev) => [...prev, ...adds]);
  };

  const onPaste = (e: React.ClipboardEvent) => {
    if (e.clipboardData.files?.length) {
      handleFiles(e.clipboardData.files);
    }
  };

  // Drag and drop
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    handleFiles(e.dataTransfer.files);
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
        if (blob.size < 600) return;
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
      if (m.role === "user" && m.attachments?.some((a) => a.kind === "image" && a.dataUrl)) {
        const parts: any[] = [{ type: "text", text: m.content || "(图片)" }];
        for (const a of m.attachments) {
          if (a.kind === "image" && a.dataUrl) {
            parts.push({ type: "image_url", image_url: { url: a.dataUrl } });
          }
        }
        return { role: "user", content: parts };
      }
      return { role: m.role, content: m.content };
    });

  const runStream = async (history: Msg[], assistantMsgId: string) => {
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const contextStr = moduleLabel
      ? `User is currently viewing the "${moduleLabel}" module (key: ${moduleKey}). Reference this module in <cite> when relevant.`
      : undefined;

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
    }
  };

  const send = async (text?: string) => {
    const content = (text ?? input).trim();
    if ((!content && attachments.length === 0) || streaming) return;

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
            className="flex w-full items-center justify-between rounded-xl border border-border bg-card px-3 py-2.5 text-sm font-medium text-foreground shadow-sm transition-all hover:border-primary/40 hover:bg-secondary hover:shadow-md"
          >
            <span className="flex items-center gap-2">
              <MessageSquarePlus className="h-4 w-4" /> 新对话
            </span>
            <kbd className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
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
              className={`group relative flex items-center gap-1 rounded-lg px-1 transition-colors ${
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
                  className="my-1 w-full rounded-md bg-background px-2 py-1.5 text-sm outline-none ring-1 ring-primary/40"
                />
              ) : (
                <button
                  onClick={() => setActiveId(c.id)}
                  className={`flex flex-1 items-center gap-1.5 truncate py-2.5 pl-2 text-left text-sm transition-colors ${
                    c.id === activeId ? "text-primary font-medium" : "text-muted-foreground group-hover:text-foreground"
                  }`}
                >
                  {c.pinned && <Pin className="h-3 w-3 shrink-0 text-amber-500" />}
                  <span className="truncate">{c.title || "新对话"}</span>
                </button>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setMenuId(menuId === c.id ? null : c.id);
                }}
                className="mr-1 hidden h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-background hover:text-foreground group-hover:flex"
              >
                <MoreHorizontal className="h-3.5 w-3.5" />
              </button>
              {menuId === c.id && (
                <div
                  onClick={(e) => e.stopPropagation()}
                  className="absolute right-1 top-10 z-20 w-36 overflow-hidden rounded-xl border border-border bg-popover py-1 shadow-xl"
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
          <button
            onClick={() => setShowShortcuts((v) => !v)}
            className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <Keyboard className="h-3.5 w-3.5" />
            <span>快捷键</span>
          </button>
          <AnimatePresence>
            {showShortcuts && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="mt-2 space-y-1 overflow-hidden"
              >
                {KEYBOARD_SHORTCUTS.map((s) => (
                  <div key={s.key} className="flex items-center justify-between px-2 py-1 text-[11px]">
                    <span className="text-muted-foreground">{s.action}</span>
                    <kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                      {s.key}
                    </kbd>
                  </div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
          <div className="mt-2 flex items-center gap-2 rounded-lg bg-gradient-to-r from-primary/5 to-transparent px-2 py-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Sparkles className="h-3.5 w-3.5" />
            </div>
            <span className="text-[11px] leading-tight text-muted-foreground">
              DTSV Intelligence
              <br />
              <span className="text-[10px]">本地保存 · 实时流式</span>
            </span>
          </div>
        </div>
      </aside>

      {/* Main chat area */}
      <div
        className="relative flex flex-1 flex-col bg-background"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {/* Drag overlay */}
        <AnimatePresence>
          {isDragging && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm"
            >
              <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-primary bg-primary/5 px-12 py-8">
                <Paperclip className="h-8 w-8 text-primary" />
                <p className="text-sm font-medium text-foreground">松开以上传文件</p>
                <p className="text-xs text-muted-foreground">支持图片、PDF、CSV、JSON 等</p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-border px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-primary/60 shadow-lg shadow-primary/20">
                <Sparkles className="h-4 w-4 text-primary-foreground" />
              </div>
              {streaming && (
                <motion.div
                  className="absolute -inset-1 rounded-xl border-2 border-primary/50"
                  animate={{ scale: [1, 1.1, 1] }}
                  transition={{ duration: 1.5, repeat: Infinity }}
                />
              )}
            </div>
            <div>
              <p className="text-sm font-semibold leading-tight text-foreground">
                DTSV Intelligence
              </p>
              <p className="text-[11px] text-muted-foreground">
                {streaming ? "正在思考..." : "数据分析智能体"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground outline-none transition-colors hover:bg-secondary focus:border-primary"
            >
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} · {m.hint}
                </option>
              ))}
            </select>
            <button
              onClick={() => setContextPanelOpen((v) => !v)}
              className={`flex h-8 w-8 items-center justify-center rounded-lg transition-colors ${
                contextPanelOpen
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
              title="上下文面板"
            >
              <PanelRight className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {isEmpty ? (
            <EmptyState
              onSend={send}
              onPickCommand={pickSlash}
              recentConversations={recentConversations}
              onSelectConversation={(id) => setActiveId(id)}
            />
          ) : (
            <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
              <AnimatePresence initial={false}>
                {active.messages.map((m, i) => {
                  const isLastAsst =
                    m.role === "assistant" && i === active.messages.length - 1;
                  const isUser = m.role === "user";
                  return (
                    <motion.div
                      key={m.id}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      className={`group flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}
                    >
                      {/* Avatar */}
                      <div className="relative shrink-0">
                        <div
                          className={`flex h-8 w-8 items-center justify-center rounded-xl ${
                            isUser
                              ? "bg-secondary text-foreground"
                              : "bg-gradient-to-br from-primary to-primary/60 text-primary-foreground shadow-lg shadow-primary/20"
                          }`}
                        >
                          {isUser ? <User className="h-4 w-4" /> : <Sparkles className="h-4 w-4" />}
                        </div>
                        {/* Animated border for AI when streaming */}
                        {!isUser && streaming && isLastAsst && (
                          <motion.div
                            className="absolute -inset-0.5 rounded-xl border-2 border-primary/30"
                            animate={{ rotate: 360 }}
                            transition={{ duration: 3, repeat: Infinity, ease: "linear" }}
                          />
                        )}
                      </div>

                      {/* Message content */}
                      <div className={`min-w-0 flex-1 ${isUser ? "text-right" : ""}`}>
                        <p className={`mb-1.5 text-[11px] font-medium text-muted-foreground ${isUser ? "text-right" : ""}`}>
                          {isUser ? "你" : "DTSV Intelligence"}
                        </p>

                        {/* Message bubble */}
                        <div className={`inline-block max-w-full ${isUser ? "text-left" : ""}`}>
                          {/* User attachments */}
                          {m.attachments && m.attachments.length > 0 && (
                            <div className={`mb-2 flex flex-wrap gap-2 ${isUser ? "justify-end" : ""}`}>
                              {m.attachments.map((a) =>
                                a.kind === "image" && a.dataUrl ? (
                                  <img
                                    key={a.id}
                                    src={a.dataUrl}
                                    alt={a.name}
                                    className="max-h-48 rounded-xl border border-border shadow-sm"
                                  />
                                ) : (
                                  <div
                                    key={a.id}
                                    className="flex items-center gap-1.5 rounded-lg border border-border bg-muted px-3 py-1.5 text-xs text-muted-foreground"
                                  >
                                    <Paperclip className="h-3 w-3" />
                                    {a.name}
                                  </div>
                                ),
                              )}
                            </div>
                          )}

                          {/* Content */}
                          {editingMsgId === m.id ? (
                            <div className="space-y-2">
                              <textarea
                                value={editingMsgVal}
                                onChange={(e) => setEditingMsgVal(e.target.value)}
                                rows={3}
                                className="w-full resize-none rounded-xl border border-primary/40 bg-card p-3 text-sm outline-none"
                              />
                              <div className="flex gap-2">
                                <button
                                  onClick={() => editUserMessage(m.id)}
                                  className="rounded-lg bg-primary px-4 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                                >
                                  重发
                                </button>
                                <button
                                  onClick={() => setEditingMsgId(null)}
                                  className="rounded-lg border border-border px-4 py-2 text-xs font-medium text-muted-foreground hover:text-foreground"
                                >
                                  取消
                                </button>
                              </div>
                            </div>
                          ) : isUser ? (
                            <div className="inline-block rounded-2xl rounded-tr-md bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                              <p className="whitespace-pre-wrap leading-relaxed">{m.content}</p>
                            </div>
                          ) : m.content === "" && streaming && isLastAsst ? (
                            <TypingIndicator />
                          ) : (
                            <div className="rounded-2xl rounded-tl-md border border-border bg-card px-4 py-3 shadow-sm">
                              <MessageRenderer content={m.content} streaming={streaming && isLastAsst} />
                            </div>
                          )}
                        </div>

                        {/* Actions */}
                        {!editingMsgId && (
                          <div className={`mt-2 flex h-6 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 ${isUser ? "justify-end" : ""}`}>
                            {!isUser ? (
                              <>
                                <CopyBtn text={segmentsToPlainText(lastSeg(m)) || m.content} />
                                <MessageFeedback messageId={m.id} />
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
            {/* Context chip */}
            {moduleLabel && (
              <div className="mb-2 flex items-center gap-1.5 text-[11px]">
                <span className="inline-flex items-center gap-1.5 rounded-lg border border-primary/20 bg-primary/5 px-2 py-1 text-muted-foreground">
                  <Sparkle className="h-3 w-3 text-primary" />
                  上下文：<span className="font-medium text-foreground">{moduleLabel}</span>
                </span>
              </div>
            )}

            {/* Attachment preview */}
            {attachments.length > 0 && (
              <div className="mb-3 flex flex-wrap gap-2">
                {attachments.map((a) => (
                  <motion.div
                    key={a.id}
                    initial={{ scale: 0.9, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    className="group/att relative flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 shadow-sm"
                  >
                    {a.kind === "image" && a.dataUrl ? (
                      <img src={a.dataUrl} className="h-8 w-8 rounded-lg object-cover" alt="" />
                    ) : (
                      <Paperclip className="h-4 w-4 text-muted-foreground" />
                    )}
                    <span className="max-w-[140px] truncate text-sm text-foreground">{a.name}</span>
                    <button
                      onClick={() => setAttachments((p) => p.filter((x) => x.id !== a.id))}
                      className="flex h-5 w-5 items-center justify-center rounded-full bg-muted text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </motion.div>
                ))}
              </div>
            )}

            {/* Image quick-prompts */}
            {attachments.some((a) => a.kind === "image") && !streaming && (
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  <Sparkle className="h-3 w-3 text-primary" /> 图片快捷提问
                </span>
                {IMAGE_QUICK_PROMPTS.map((q) => (
                  <button
                    key={q}
                    onClick={() => send(q)}
                    className="rounded-full border border-border bg-card px-3 py-1.5 text-xs text-foreground transition-all hover:border-primary/50 hover:bg-primary/5 hover:text-primary"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}

            {/* Input area with glow effect */}
            <div className="relative">
              {/* Glow effect when focused */}
              <div className="absolute -inset-1 rounded-2xl bg-gradient-to-r from-primary/20 via-primary/10 to-primary/20 opacity-0 blur-xl transition-opacity focus-within:opacity-100" />
              
              <div className="relative flex items-end gap-2 rounded-2xl border border-border bg-card px-4 py-3 shadow-sm transition-all focus-within:border-primary/50 focus-within:shadow-lg">
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
                  accept="image/*,.pdf,.csv,.txt,.json,.md"
                  hidden
                  onChange={(e) => {
                    handleFiles(e.target.files);
                    e.currentTarget.value = "";
                  }}
                />
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  className="mb-0.5 flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  title="附件 (图片 / 文件)"
                >
                  <Paperclip className="h-5 w-5" />
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
                  placeholder="提问数据、要求总结，或输入 / 调用命令...  (Shift + Enter 换行)"
                  className="max-h-[200px] min-h-[32px] flex-1 resize-none bg-transparent py-1.5 text-sm leading-relaxed text-foreground outline-none placeholder:text-muted-foreground"
                />
                <button
                  type="button"
                  onClick={recording ? stopRecording : startRecording}
                  disabled={transcribing}
                  className={`mb-0.5 flex h-9 w-9 items-center justify-center rounded-xl transition-all ${
                    recording
                      ? "bg-destructive text-destructive-foreground shadow-lg shadow-destructive/25 hover:bg-destructive/90"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  } disabled:opacity-50`}
                  title={recording ? "停止录音" : transcribing ? "转写中..." : "语音输入"}
                >
                  {transcribing ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : recording ? (
                    <Square className="h-4 w-4 fill-current" />
                  ) : (
                    <Mic className="h-5 w-5" />
                  )}
                </button>
                {streaming ? (
                  <button
                    onClick={stop}
                    className="mb-0.5 flex h-9 w-9 items-center justify-center rounded-xl bg-foreground text-background shadow-lg transition-all hover:opacity-90"
                    title="停止生成"
                  >
                    <StopCircle className="h-5 w-5" />
                  </button>
                ) : (
                  <button
                    onClick={() => send()}
                    disabled={!input.trim() && attachments.length === 0}
                    className="mb-0.5 flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-lg shadow-primary/25 transition-all hover:bg-primary/90 disabled:opacity-40 disabled:shadow-none"
                  >
                    <ArrowUp className="h-5 w-5" />
                  </button>
                )}
              </div>
            </div>
            <p className="mt-3 text-center text-[11px] text-muted-foreground">
              DTSV Intelligence 可能出错，请核对关键数据。
            </p>
          </div>
        </div>
      </div>

      {/* Context Panel */}
      <ContextPanel
        isOpen={contextPanelOpen}
        onToggle={() => setContextPanelOpen((v) => !v)}
        attachments={attachments}
        moduleLabel={moduleLabel}
        onRemoveAttachment={(id) => setAttachments((p) => p.filter((a) => a.id !== id))}
      />
    </div>
  );
};

// ----- small sub-components -----

const TypingIndicator = () => (
  <div className="flex items-center gap-2 rounded-2xl rounded-tl-md border border-border bg-card px-4 py-3 shadow-sm">
    <div className="flex items-center gap-1">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="h-2 w-2 rounded-full bg-primary"
          animate={{
            scale: [1, 1.2, 1],
            opacity: [0.5, 1, 0.5],
          }}
          transition={{
            duration: 1,
            repeat: Infinity,
            delay: i * 0.15,
          }}
        />
      ))}
    </div>
    <span className="text-xs text-muted-foreground">正在思考...</span>
  </div>
);

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
    className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-accent ${
      destructive ? "text-destructive" : "text-foreground"
    }`}
  >
    <Icon className="h-3.5 w-3.5" />
    {children}
  </button>
);

export default AIChat;
