import { useState } from "react";
import { Beaker, Microscope, Rocket, Sparkles, Users, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

export interface MissionPayload {
  prompt: string;
}

interface Mission {
  id: string;
  icon: any;
  title: string;
  subtitle: string;
  agents: string[];
  accent: string;
  enabled: boolean;
}

const MISSIONS: Mission[] = [
  {
    id: "testcases",
    icon: Beaker,
    title: "测试用例生成",
    subtitle: "5 智能体协作 · 接地历史缺陷",
    agents: ["Defect Miner", "Coverage Analyst", "Risk Heuristics", "Generator", "Critic"],
    accent: "from-emerald-500/15 to-emerald-500/5 text-emerald-600 dark:text-emerald-400",
    enabled: true,
  },
  {
    id: "rca",
    icon: Microscope,
    title: "根因分析",
    subtitle: "即将开放",
    agents: [],
    accent: "from-amber-500/10 to-amber-500/5 text-amber-600/70",
    enabled: false,
  },
  {
    id: "release",
    icon: Rocket,
    title: "发版评审",
    subtitle: "即将开放",
    agents: [],
    accent: "from-sky-500/10 to-sky-500/5 text-sky-600/70",
    enabled: false,
  },
];

const TIME_WINDOWS = [
  { id: "30", label: "近 30 天" },
  { id: "90", label: "近 90 天" },
  { id: "180", label: "近 180 天" },
];

const COVERAGE_TYPES = [
  { id: "function", label: "功能" },
  { id: "boundary", label: "边界" },
  { id: "exception", label: "异常" },
  { id: "regression", label: "回归" },
  { id: "performance", label: "性能" },
];

interface Props {
  onLaunch: (p: MissionPayload) => void;
  compact?: boolean;
}

export default function MissionLauncher({ onLaunch, compact }: Props) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <>
      <div className={`flex flex-wrap items-stretch gap-2 ${compact ? "mb-2" : ""}`}>
        <div className="flex items-center gap-1.5 px-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <Sparkles className="h-3 w-3 text-primary" />
          Agent Mission
        </div>
        {MISSIONS.map((m) => (
          <button
            key={m.id}
            disabled={!m.enabled}
            onClick={() => setOpen(m.id)}
            className={`group relative flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-1.5 text-left transition-all ${
              m.enabled
                ? "hover:border-primary/40 hover:shadow-sm cursor-pointer"
                : "opacity-50 cursor-not-allowed"
            }`}
          >
            <div className={`flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br ${m.accent}`}>
              <m.icon className="h-3.5 w-3.5" />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-semibold leading-tight text-foreground">{m.title}</p>
              <p className="text-[10px] leading-tight text-muted-foreground">{m.subtitle}</p>
            </div>
            {m.enabled && (
              <span className="ml-1 inline-flex items-center gap-1 rounded-full bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">
                <Users className="h-2.5 w-2.5" />
                {m.agents.length}
              </span>
            )}
          </button>
        ))}
      </div>

      <AnimatePresence>
        {open === "testcases" && (
          <TestCaseMissionDialog
            onClose={() => setOpen(null)}
            onLaunch={(prompt) => {
              setOpen(null);
              onLaunch({ prompt });
            }}
          />
        )}
      </AnimatePresence>
    </>
  );
}

function TestCaseMissionDialog({
  onClose,
  onLaunch,
}: {
  onClose: () => void;
  onLaunch: (prompt: string) => void;
}) {
  const [target, setTarget] = useState("");
  const [window, setWindow] = useState("90");
  const [count, setCount] = useState(6);
  const [types, setTypes] = useState<string[]>(["function", "boundary", "exception"]);

  const toggleType = (id: string) =>
    setTypes((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const canLaunch = target.trim().length > 0 && types.length > 0;

  const handleLaunch = () => {
    if (!canLaunch) return;
    const typeLabels = COVERAGE_TYPES.filter((t) => types.includes(t.id)).map((t) => t.label).join("、");
    const win = TIME_WINDOWS.find((w) => w.id === window)?.label ?? "近 90 天";
    const prompt =
      `【任务发射台 · 测试用例生成】\n` +
      `分析目标：${target.trim()}\n` +
      `时间窗口：${win}\n` +
      `期望数量：${count} 条\n` +
      `用例类型：${typeLabels}\n\n` +
      `请严格按【测试用例生成协议】执行多智能体工作流：\n` +
      `1. Defect Miner：先 list_tables/profile_table 确认数据，再从 topissue 抽取该目标在${win}内的真实高频/高严重度缺陷与失败模式（必须有数字依据）。\n` +
      `2. Coverage Gap Analyst：从 coverage 找出该目标范围内未覆盖或覆盖率偏低的需求点。\n` +
      `3. Risk Heuristics：用 risk_scan 识别数据异常面。\n` +
      `4. Generator：产出 ${count} 条用例，每条 linked_defect 或 linked_req 至少其一，rationale 必须引用真实数字；类型必须覆盖：${typeLabels}。\n` +
      `5. Critic：自检并丢弃任何似是而非、与业务无关的用例，给出最终清单。\n\n` +
      `最终以 <testcases module="${target.trim()}"> JSON 数组形式返回。`;
    onLaunch(prompt);
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 8 }}
        transition={{ duration: 0.15 }}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-border bg-gradient-to-br from-emerald-500/8 to-transparent px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
              <Beaker className="h-4.5 w-4.5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-foreground">测试用例生成 · 任务发射台</h3>
              <p className="mt-0.5 text-xs text-muted-foreground">
                5 智能体协作 · 接地真实缺陷与覆盖率数据
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          {/* Agent squad preview */}
          <div className="rounded-lg border border-border bg-muted/30 p-2.5">
            <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Agent Squad
            </p>
            <div className="flex flex-wrap gap-1.5">
              {["Defect Miner", "Coverage Analyst", "Risk Heuristics", "Generator", "Critic"].map((a, i) => (
                <span
                  key={a}
                  className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-0.5 text-[10px] font-medium text-foreground"
                >
                  <span className="text-muted-foreground">{i + 1}</span>
                  {a}
                </span>
              ))}
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground">
              分析目标 <span className="text-muted-foreground">（模块 / 需求 ID / 缺陷 ID）</span>
            </label>
            <input
              autoFocus
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="例如：OTA 升级、CAN-BMS、REQ-12、BUG-2451"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition-colors focus:border-primary"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-foreground">时间窗口</label>
              <div className="flex gap-1">
                {TIME_WINDOWS.map((w) => (
                  <button
                    key={w.id}
                    onClick={() => setWindow(w.id)}
                    className={`flex-1 rounded-md border px-2 py-1.5 text-xs transition-colors ${
                      window === w.id
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border bg-background text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {w.label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-foreground">
                生成数量 <span className="text-muted-foreground">· {count} 条</span>
              </label>
              <input
                type="range"
                min={3}
                max={12}
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
                className="h-9 w-full accent-primary"
              />
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground">用例类型</label>
            <div className="flex flex-wrap gap-1.5">
              {COVERAGE_TYPES.map((t) => {
                const active = types.includes(t.id);
                return (
                  <button
                    key={t.id}
                    onClick={() => toggleType(t.id)}
                    className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                      active
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border bg-background text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {t.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border bg-muted/20 px-5 py-3">
          <p className="text-[11px] text-muted-foreground">
            启动后将在 Canvas 中展开 Agent 工作流
          </p>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
            >
              取消
            </button>
            <button
              onClick={handleLaunch}
              disabled={!canLaunch}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              <Rocket className="h-3 w-3" />
              启动任务
            </button>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
