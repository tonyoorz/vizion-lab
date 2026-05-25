import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { motion, AnimatePresence } from "framer-motion";
import {
  Brain,
  ChevronDown,
  ChevronRight,
  Check,
  Copy,
  Database,
  Loader2,
  Wrench,
  Clock,
  CheckCircle2,
  Sparkles,
  BarChart3,
  Table2,
  FileSearch,
  Cpu,
} from "lucide-react";
import { parseAgentStream } from "./agentParser";

interface Props {
  content: string;
  streaming: boolean;
}

// Replaces <cite source="x">label</cite> in markdown with a custom token,
// then renders a clickable chip via remark-react custom components.
function preprocessCitations(md: string) {
  return md.replace(
    /<cite\s+source="([^"]+)">([^<]+)<\/cite>/gi,
    (_m, src, label) => `[${label}](#cite:${encodeURIComponent(src)})`,
  );
}

export default function MessageRenderer({ content, streaming }: Props) {
  const segs = parseAgentStream(content);
  return (
    <div className="space-y-3">
      {segs.map((s, i) => {
        if (s.kind === "think")
          return <ThinkBlock key={i} text={s.text} closed={s.closed} streaming={streaming} />;
        if (s.kind === "step")
          return (
            <StepBlock
              key={i}
              title={s.title}
              source={s.source}
              text={s.text}
              closed={s.closed}
              index={i}
            />
          );
        return <TextBlock key={i} text={s.text} />;
      })}
    </div>
  );
}

// Enhanced thinking block with animation and summary
function ThinkBlock({
  text,
  closed,
  streaming,
}: {
  text: string;
  closed: boolean;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(false);
  const active = streaming && !closed;
  
  // Generate a brief summary from the thinking text
  const summary = text.trim().split('\n')[0]?.slice(0, 60) || "分析中...";
  const thinkingSteps = text.trim().split('\n').filter(line => line.trim()).length;

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      className="group relative overflow-hidden rounded-xl border border-dashed border-primary/20 bg-gradient-to-br from-primary/5 to-transparent"
    >
      {/* Animated gradient border when active */}
      {active && (
        <motion.div
          className="absolute inset-0 rounded-xl"
          style={{
            background: "linear-gradient(90deg, transparent, rgba(59, 130, 246, 0.1), transparent)",
            backgroundSize: "200% 100%",
          }}
          animate={{
            backgroundPosition: ["0% 0%", "200% 0%"],
          }}
          transition={{
            duration: 2,
            repeat: Infinity,
            ease: "linear",
          }}
        />
      )}
      
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-primary/5"
      >
        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
          active 
            ? "bg-primary/20 text-primary" 
            : "bg-muted text-muted-foreground"
        }`}>
          {active ? (
            <motion.div
              animate={{ rotate: 360 }}
              transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
            >
              <Brain className="h-4 w-4" />
            </motion.div>
          ) : (
            <Brain className="h-4 w-4" />
          )}
        </div>
        
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-foreground">
              {active ? "正在思考..." : "思考过程"}
            </span>
            {!active && (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                {thinkingSteps} 步
              </span>
            )}
          </div>
          {!open && (
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
              {summary}...
            </p>
          )}
        </div>
        
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>
      
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="border-t border-dashed border-primary/10 px-4 py-3">
              <div className="space-y-2">
                {text.trim().split('\n').filter(line => line.trim()).map((line, idx) => (
                  <motion.div
                    key={idx}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: idx * 0.05 }}
                    className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground"
                  >
                    <span className="mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[9px] font-medium text-primary">
                      {idx + 1}
                    </span>
                    <span>{line.trim()}</span>
                  </motion.div>
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// Get icon based on step title/source
function getStepIcon(title: string, source?: string) {
  const lower = (title + (source || "")).toLowerCase();
  if (lower.includes("sql") || lower.includes("query") || lower.includes("数据库")) return Database;
  if (lower.includes("chart") || lower.includes("图表") || lower.includes("可视化")) return BarChart3;
  if (lower.includes("table") || lower.includes("表格")) return Table2;
  if (lower.includes("search") || lower.includes("搜索") || lower.includes("检索")) return FileSearch;
  if (lower.includes("分析") || lower.includes("计算")) return Cpu;
  return Wrench;
}

// Enhanced tool/step block with status, progress, and expandable results
function StepBlock({
  title,
  source,
  text,
  closed,
  index,
}: {
  title: string;
  source?: string;
  text: string;
  closed: boolean;
  index: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [elapsedTime, setElapsedTime] = useState(0);
  const Icon = getStepIcon(title, source);
  const hasResult = text.trim().length > 0;
  
  // Track elapsed time while running
  useEffect(() => {
    if (!closed) {
      const start = Date.now();
      const interval = setInterval(() => {
        setElapsedTime(Math.floor((Date.now() - start) / 100) / 10);
      }, 100);
      return () => clearInterval(interval);
    }
  }, [closed]);

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.1 }}
      className="group overflow-hidden rounded-xl border border-border bg-card shadow-sm transition-shadow hover:shadow-md"
    >
      {/* Header */}
      <div
        onClick={() => hasResult && setExpanded((v) => !v)}
        className={`flex items-center gap-3 px-4 py-3 ${hasResult ? "cursor-pointer" : ""}`}
      >
        {/* Status icon with animation */}
        <div className={`relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors ${
          closed 
            ? "bg-emerald-500/10 text-emerald-500" 
            : "bg-primary/10 text-primary"
        }`}>
          {closed ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <>
              <motion.div
                className="absolute inset-0 rounded-lg bg-primary/20"
                animate={{ scale: [1, 1.2, 1] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              />
              <Loader2 className="relative h-4 w-4 animate-spin" />
            </>
          )}
        </div>
        
        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Icon className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">{title}</span>
          </div>
          <div className="mt-0.5 flex items-center gap-2">
            {source && (
              <span className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                <Database className="h-2.5 w-2.5" />
                {source}
              </span>
            )}
            {!closed && (
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Clock className="h-2.5 w-2.5" />
                {elapsedTime.toFixed(1)}s
              </span>
            )}
            {closed && (
              <span className="flex items-center gap-1 text-[10px] text-emerald-500">
                <Check className="h-2.5 w-2.5" />
                完成
              </span>
            )}
          </div>
        </div>
        
        {/* Expand button */}
        {hasResult && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
              {expanded ? "收起" : "展开结果"}
            </span>
            <ChevronRight
              className={`h-4 w-4 text-muted-foreground transition-transform ${
                expanded ? "rotate-90" : ""
              }`}
            />
          </div>
        )}
      </div>
      
      {/* Progress bar when running */}
      {!closed && (
        <div className="h-0.5 w-full overflow-hidden bg-muted">
          <motion.div
            className="h-full bg-gradient-to-r from-primary/50 via-primary to-primary/50"
            initial={{ x: "-100%" }}
            animate={{ x: "100%" }}
            transition={{ duration: 1.5, repeat: Infinity, ease: "linear" }}
          />
        </div>
      )}
      
      {/* Expandable result */}
      <AnimatePresence initial={false}>
        {expanded && hasResult && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="border-t border-border bg-muted/30 px-4 py-3">
              <div className="flex items-center gap-2 mb-2">
                <Sparkles className="h-3 w-3 text-primary" />
                <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  执行结果
                </span>
              </div>
              <pre className="overflow-x-auto rounded-lg bg-card p-3 text-xs leading-relaxed text-foreground">
                {text.trim()}
              </pre>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function TextBlock({ text }: { text: string }) {
  const md = preprocessCitations(text);
  return (
    <div className="prose prose-sm max-w-none break-words text-foreground prose-headings:font-semibold prose-headings:text-foreground prose-p:my-2 prose-p:leading-relaxed prose-strong:text-foreground prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:font-normal prose-code:before:content-none prose-code:after:content-none prose-ol:my-2 prose-ul:my-2 prose-li:my-0.5 prose-table:text-sm">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            if (href?.startsWith("#cite:")) {
              const source = decodeURIComponent(href.slice(6));
              return <CitationChip source={source}>{children}</CitationChip>;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" className="text-primary underline-offset-2 hover:underline">
                {children}
              </a>
            );
          },
          pre({ children }) {
            return <CodeBlock>{children}</CodeBlock>;
          },
        }}
      >
        {md}
      </ReactMarkdown>
    </div>
  );
}

function CitationChip({ source, children }: { source: string; children: any }) {
  return (
    <span
      title={`来源: ${source}`}
      className="mx-0.5 inline-flex items-center gap-1 rounded-md border border-primary/20 bg-primary/5 px-1.5 py-0 align-baseline text-[11px] font-medium text-primary no-underline transition-colors hover:bg-primary/10"
    >
      <Database className="h-2.5 w-2.5" />
      {children}
    </span>
  );
}

function CodeBlock({ children }: { children: any }) {
  const [copied, setCopied] = useState(false);
  const codeText =
    (children?.props?.children as string) ??
    (typeof children === "string" ? children : "");
  return (
    <div className="group relative my-3 overflow-hidden rounded-xl border border-border bg-[hsl(220_15%_12%)]">
      {/* Language badge */}
      <div className="flex items-center justify-between border-b border-white/5 bg-white/5 px-4 py-2">
        <span className="text-[10px] font-medium uppercase tracking-wider text-white/50">
          Code
        </span>
        <button
          onClick={() => {
            navigator.clipboard.writeText(String(codeText));
            setCopied(true);
            setTimeout(() => setCopied(false), 1400);
          }}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-white/50 transition-colors hover:bg-white/10 hover:text-white"
          title="复制代码"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3" />
              <span>已复制</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      <pre className="!my-0 !bg-transparent overflow-x-auto p-4 text-[12.5px] leading-relaxed text-white/90">
        {children}
      </pre>
    </div>
  );
}
