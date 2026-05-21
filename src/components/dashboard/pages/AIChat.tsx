import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  BarChart3,
  Bug,
  Compass,
  MessageSquarePlus,
  Paperclip,
  Sparkles,
  StopCircle,
  TrendingUp,
  User,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { motion, AnimatePresence } from "framer-motion";

type Role = "user" | "assistant";
interface Msg {
  role: Role;
  content: string;
}
interface Conversation {
  id: string;
  title: string;
  messages: Msg[];
  updatedAt: number;
}

const MODELS = [
  { id: "google/gemini-3-flash-preview", label: "Gemini 3 · Flash", hint: "默认 · 快速" },
  { id: "google/gemini-2.5-pro", label: "Gemini 2.5 · Pro", hint: "深度推理" },
  { id: "openai/gpt-5-mini", label: "GPT-5 · Mini", hint: "平衡" },
  { id: "openai/gpt-5", label: "GPT-5", hint: "最强" },
];

const SUGGESTIONS = [
  {
    icon: TrendingUp,
    title: "Top Issue 趋势诊断",
    prompt: "请基于近三个月的 Top Issue 数据，识别上升最快的三个问题模块并给出根因假设。",
  },
  {
    icon: Bug,
    title: "高频缺陷根因",
    prompt: "在缺陷高频分析里，最近一周新增缺陷集中在哪些 ECU？是否与某次回归提交相关？",
  },
  {
    icon: BarChart3,
    title: "覆盖率风险面",
    prompt: "覆盖率低于 70% 的模块有哪些？请按业务影响排序，并建议本周补测顺序。",
  },
  {
    icon: Compass,
    title: "团队产能复盘",
    prompt: "对比各测试小组的执行效率与缺陷发现率，输出一份本周复盘要点。",
  },
];

const newConversation = (): Conversation => ({
  id: crypto.randomUUID(),
  title: "新对话",
  messages: [],
  updatedAt: Date.now(),
});

const AIChat = () => {
  const [conversations, setConversations] = useState<Conversation[]>([newConversation()]);
  const [activeId, setActiveId] = useState(conversations[0].id);
  const [input, setInput] = useState("");
  const [model, setModel] = useState(MODELS[0].id);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const active = conversations.find((c) => c.id === activeId)!;
  const isEmpty = active.messages.length === 0;

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [active.messages]);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  }, [input]);

  const updateActive = (fn: (c: Conversation) => Conversation) =>
    setConversations((prev) => prev.map((c) => (c.id === activeId ? fn(c) : c)));

  const handleNew = () => {
    const c = newConversation();
    setConversations((prev) => [c, ...prev]);
    setActiveId(c.id);
    setInput("");
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  };

  const send = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || streaming) return;

    const userMsg: Msg = { role: "user", content };
    const history = [...active.messages, userMsg];
    updateActive((c) => ({
      ...c,
      title: c.messages.length === 0 ? content.slice(0, 28) : c.title,
      messages: [...history, { role: "assistant", content: "" }],
      updatedAt: Date.now(),
    }));
    setInput("");
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const url = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/chat`;
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
        },
        body: JSON.stringify({ messages: history, model }),
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        const err = await resp.json().catch(() => ({ error: "请求失败" }));
        updateActive((c) => {
          const msgs = [...c.messages];
          msgs[msgs.length - 1] = {
            role: "assistant",
            content: `⚠️ ${err.error || "请求失败，请稍后再试。"}`,
          };
          return { ...c, messages: msgs };
        });
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
              updateActive((c) => {
                const msgs = [...c.messages];
                msgs[msgs.length - 1] = { role: "assistant", content: acc };
                return { ...c, messages: msgs };
              });
            }
          } catch {
            buffer = line + "\n" + buffer;
            break;
          }
        }
      }
    } catch (e: any) {
      if (e.name !== "AbortError") {
        updateActive((c) => {
          const msgs = [...c.messages];
          msgs[msgs.length - 1] = {
            role: "assistant",
            content: "⚠️ 连接中断，请重试。",
          };
          return { ...c, messages: msgs };
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  };

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
          <p className="filter-label px-1">最近</p>
        </div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {conversations.map((c) => (
            <button
              key={c.id}
              onClick={() => setActiveId(c.id)}
              className={`group flex w-full items-center gap-2 truncate rounded-md px-2.5 py-2 text-left text-sm transition-colors ${
                c.id === activeId
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              <span className="truncate">{c.title || "新对话"}</span>
            </button>
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
              <span className="text-[10px]">实时流式响应</span>
            </span>
          </div>
        </div>
      </aside>

      {/* Main chat area */}
      <div className="flex flex-1 flex-col bg-background">
        {/* Top bar with model picker */}
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
                数据分析智能体 · 支持工具调用
              </p>
            </div>
          </div>
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
                  问我关于缺陷趋势、覆盖率、团队产能或具体项目的问题。
                </p>
              </motion.div>
              <div className="grid w-full gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((s, i) => (
                  <motion.button
                    key={s.title}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.05 * i }}
                    onClick={() => send(s.prompt)}
                    className="group rounded-xl border border-border bg-card p-3.5 text-left transition-all hover:border-primary/40 hover:shadow-sm"
                  >
                    <div className="mb-1.5 flex h-7 w-7 items-center justify-center rounded-md bg-muted text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
                      <s.icon className="h-3.5 w-3.5" />
                    </div>
                    <p className="text-sm font-medium text-foreground">
                      {s.title}
                    </p>
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
                {active.messages.map((m, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex gap-3"
                  >
                    <div
                      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
                        m.role === "user"
                          ? "bg-secondary text-foreground"
                          : "bg-gradient-to-br from-primary to-primary/60 text-primary-foreground"
                      }`}
                    >
                      {m.role === "user" ? (
                        <User className="h-3.5 w-3.5" />
                      ) : (
                        <Sparkles className="h-3.5 w-3.5" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1 pt-0.5">
                      <p className="mb-1 text-xs font-medium text-muted-foreground">
                        {m.role === "user" ? "你" : "DTSV Intelligence"}
                      </p>
                      {m.role === "assistant" && m.content === "" && streaming ? (
                        <TypingDots />
                      ) : (
                        <div className="prose prose-sm max-w-none break-words text-foreground prose-headings:font-semibold prose-headings:text-foreground prose-p:my-2 prose-p:leading-relaxed prose-strong:text-foreground prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:font-normal prose-code:before:content-none prose-code:after:content-none prose-pre:rounded-lg prose-pre:bg-muted prose-pre:text-foreground prose-ol:my-2 prose-ul:my-2 prose-li:my-0.5 prose-table:text-sm">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {m.content}
                          </ReactMarkdown>
                        </div>
                      )}
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-border bg-background px-6 py-4">
          <div className="mx-auto max-w-3xl">
            <div className="relative flex items-end gap-2 rounded-2xl border border-border bg-card px-3 py-2 shadow-sm transition-colors focus-within:border-primary/50 focus-within:shadow-md">
              <button
                type="button"
                className="mb-1 flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title="附件"
              >
                <Paperclip className="h-4 w-4" />
              </button>
              <textarea
                ref={taRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                rows={1}
                placeholder="提问数据、要求总结，或让我提建议…  (Shift + Enter 换行)"
                className="max-h-[200px] min-h-[28px] flex-1 resize-none bg-transparent py-1.5 text-sm leading-relaxed text-foreground outline-none placeholder:text-muted-foreground"
              />
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
                  disabled={!input.trim()}
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

export default AIChat;
