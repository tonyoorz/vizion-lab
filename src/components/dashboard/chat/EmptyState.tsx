import { motion } from "framer-motion";
import {
  Sparkles,
  BarChart3,
  Database,
  FileSearch,
  Cpu,
  MessageSquare,
  Zap,
  Brain,
  History,
} from "lucide-react";
import { SlashCommand, SLASH_COMMANDS } from "./SlashMenu";

interface Props {
  onSend: (text: string) => void;
  onPickCommand: (cmd: SlashCommand) => void;
  recentConversations?: { id: string; title: string }[];
  onSelectConversation?: (id: string) => void;
}

const CAPABILITIES = [
  {
    icon: BarChart3,
    title: "数据可视化",
    description: "生成图表、趋势分析",
  },
  {
    icon: Database,
    title: "SQL 查询",
    description: "自然语言转 SQL",
  },
  {
    icon: FileSearch,
    title: "智能检索",
    description: "多数据源搜索整合",
  },
  {
    icon: Cpu,
    title: "深度分析",
    description: "复杂数据推理",
  },
];

const QUICK_PROMPTS = [
  "分析过去一周的缺陷趋势",
  "找出最常见的问题类型",
  "对比各项目的质量指标",
  "生成本月质量报告摘要",
];

export default function EmptyState({
  onSend,
  onPickCommand,
  recentConversations = [],
  onSelectConversation,
}: Props) {
  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center px-6 py-12">
      {/* Hero section with animated glow */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="relative mb-10 text-center"
      >
        {/* Animated glow background */}
        <div className="absolute inset-0 -z-10 flex items-center justify-center">
          <motion.div
            className="h-32 w-32 rounded-full bg-primary/20 blur-3xl"
            animate={{
              scale: [1, 1.2, 1],
              opacity: [0.3, 0.5, 0.3],
            }}
            transition={{
              duration: 4,
              repeat: Infinity,
              ease: "easeInOut",
            }}
          />
        </div>
        
        {/* Logo */}
        <motion.div
          initial={{ scale: 0.8 }}
          animate={{ scale: 1 }}
          transition={{ type: "spring", stiffness: 200, damping: 15 }}
          className="relative mx-auto mb-6 flex h-16 w-16 items-center justify-center"
        >
          {/* Rotating ring */}
          <motion.div
            className="absolute inset-0 rounded-2xl border-2 border-dashed border-primary/30"
            animate={{ rotate: 360 }}
            transition={{ duration: 20, repeat: Infinity, ease: "linear" }}
          />
          {/* Inner solid */}
          <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-primary/60 shadow-lg shadow-primary/25">
            <Sparkles className="h-7 w-7 text-primary-foreground" />
          </div>
        </motion.div>
        
        <h1 className="text-balance text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
          你好，今天要分析什么？
        </h1>
        <p className="mt-3 text-pretty text-sm text-muted-foreground">
          我是 DTSV Intelligence，你的数据分析智能助手。
          <br className="hidden sm:block" />
          支持自然语言查询、图表生成和深度分析。
        </p>
      </motion.div>

      {/* Capabilities grid */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.1 }}
        className="mb-8 grid w-full gap-3 sm:grid-cols-4"
      >
        {CAPABILITIES.map((cap, i) => (
          <motion.div
            key={cap.title}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 + i * 0.05 }}
            className="group flex flex-col items-center rounded-xl border border-border bg-card/50 p-4 text-center transition-all hover:border-primary/30 hover:bg-card hover:shadow-sm"
          >
            <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
              <cap.icon className="h-5 w-5" />
            </div>
            <p className="text-sm font-medium text-foreground">{cap.title}</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {cap.description}
            </p>
          </motion.div>
        ))}
      </motion.div>

      {/* Quick prompts */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.25 }}
        className="mb-8 w-full"
      >
        <div className="mb-3 flex items-center gap-2">
          <Zap className="h-4 w-4 text-primary" />
          <span className="text-xs font-semibold text-foreground">快速开始</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {QUICK_PROMPTS.map((prompt, i) => (
            <motion.button
              key={prompt}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.3 + i * 0.05 }}
              onClick={() => onSend(prompt)}
              className="rounded-full border border-border bg-card px-4 py-2 text-sm text-foreground transition-all hover:border-primary/50 hover:bg-primary/5 hover:text-primary"
            >
              {prompt}
            </motion.button>
          ))}
        </div>
      </motion.div>

      {/* Slash commands preview */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.35 }}
        className="w-full"
      >
        <div className="mb-3 flex items-center gap-2">
          <Brain className="h-4 w-4 text-primary" />
          <span className="text-xs font-semibold text-foreground">预设命令</span>
          <kbd className="ml-1 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            /
          </kbd>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {SLASH_COMMANDS.slice(0, 4).map((cmd, i) => (
            <motion.button
              key={cmd.id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.4 + i * 0.05 }}
              onClick={() => onPickCommand(cmd)}
              className="group flex items-start gap-3 rounded-xl border border-border bg-card p-4 text-left transition-all hover:border-primary/40 hover:shadow-sm"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
                <cmd.icon className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium text-foreground">
                    {cmd.label.replace("/", "")}
                  </p>
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {cmd.label}
                  </span>
                </div>
                <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                  {cmd.description}
                </p>
              </div>
            </motion.button>
          ))}
        </div>
      </motion.div>

      {/* Recent conversations */}
      {recentConversations.length > 0 && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="mt-8 w-full"
        >
          <div className="mb-3 flex items-center gap-2">
            <History className="h-4 w-4 text-muted-foreground" />
            <span className="text-xs font-semibold text-muted-foreground">最近对话</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {recentConversations.slice(0, 5).map((conv) => (
              <button
                key={conv.id}
                onClick={() => onSelectConversation?.(conv.id)}
                className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/30 hover:text-foreground"
              >
                <MessageSquare className="h-3 w-3" />
                <span className="max-w-[120px] truncate">{conv.title}</span>
              </button>
            ))}
          </div>
        </motion.div>
      )}

      {/* Tips */}
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.6 }}
        className="mt-10 text-center text-[11px] text-muted-foreground"
      >
        提示：可以粘贴图片进行分析，或使用 
        <kbd className="mx-1 rounded bg-muted px-1 py-0.5 font-mono text-[10px]">Shift + Enter</kbd> 
        换行
      </motion.p>
    </div>
  );
}
