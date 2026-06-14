import { useEffect, useRef } from "react";
import {
  BarChart3,
  Bug,
  Compass,
  Database,
  FileBarChart,
  GaugeCircle,
  LineChart,
  ShieldAlert,
  TrendingUp,
  ClipboardList,
} from "lucide-react";

export interface SlashCommand {
  id: string;
  label: string;
  hint: string;
  prompt: string;
  icon: any;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    id: "gen-testcases",
    label: "/生成测试用例",
    hint: "多智能体接地：历史缺陷 + 覆盖率缺口 + 自批判，产出可落地用例",
    prompt:
      "请按【测试用例生成协议】为我生成测试用例。我感兴趣的模块/需求是：__（请补充，例如 OTA、CAN-BMS、REQ-12 或某条缺陷 ID）__。务必先调用 list_tables 与 profile_table 确认现有数据，然后从 topissue 抽取该模块近 90 天高频/高严重度的真实缺陷与失败模式，再从 coverage 找出未覆盖的需求点，最后产出 5-8 条用例并以 <testcases module=\"...\"> JSON 数组形式返回，每条用例必须 linked_defect 或 linked_req 至少有其一，rationale 必须引用真实数字。最后用 Critic 角色自检并丢弃任何似是而非、与业务无关的用例。",
    icon: ClipboardList,
  },
  {
    id: "risk-scan",
    label: "/数据风险扫描",
    hint: "对已连接的本地数据做风险扫描，给出 Insights",
    prompt: "对当前已连接的本地 DuckDB 数据做完整风险扫描：先列出表，再对每张关键表调用 risk_scan，并对最严重的发现写 SQL 取证、给出图表和行动建议。",
    icon: ShieldAlert,
  },
  {
    id: "explore-db",
    label: "/探查我的数据",
    hint: "列出表 + 列出 schema + 给出可分析方向",
    prompt: "列出我已连接的所有表，对每张表做 profile_table，然后给出 5 个值得深入分析的角度。",
    icon: Database,
  },
  {
    id: "top-issue",
    label: "/Top Issue 诊断",
    hint: "近三月上升最快的问题模块 + 根因假设",
    prompt: "请基于近三个月的 Top Issue 数据，识别上升最快的三个问题模块并给出根因假设。",
    icon: TrendingUp,
  },
  {
    id: "coverage",
    label: "/覆盖率风险面",
    hint: "低于阈值的模块 + 补测顺序",
    prompt: "覆盖率低于 70% 的模块有哪些？请按业务影响排序，并建议本周补测顺序。",
    icon: GaugeCircle,
  },
  {
    id: "defect-freq",
    label: "/高频缺陷根因",
    hint: "最近一周新增缺陷的 ECU 聚类与回归关联",
    prompt: "在缺陷高频分析里，最近一周新增缺陷集中在哪些 ECU？是否与某次回归提交相关？",
    icon: Bug,
  },
  {
    id: "team",
    label: "/团队产能复盘",
    hint: "各小组执行效率与缺陷发现率对比",
    prompt: "对比各测试小组的执行效率与缺陷发现率，输出一份本周复盘要点。",
    icon: Compass,
  },
  {
    id: "weekly",
    label: "/生成周报",
    hint: "本周质量摘要（KPI / 风险 / 建议）",
    prompt: "请基于本周仪表盘数据生成一份精炼周报，包含 KPI 摘要、3 个核心风险与建议下一步。",
    icon: FileBarChart,
  },
  {
    id: "trend",
    label: "/趋势预测",
    hint: "缺陷趋势外推 2 周",
    prompt: "基于过去 8 周的缺陷与解决趋势，预测未来 2 周可能的走势并指出最大不确定性。",
    icon: LineChart,
  },
  {
    id: "explain-chart",
    label: "/解释当前图表",
    hint: "总结当前页可视化的关键洞察",
    prompt: "请总结我当前所在页面图表的关键洞察，3 条以内。",
    icon: BarChart3,
  },
];

interface Props {
  query: string;
  onPick: (cmd: SlashCommand) => void;
  onClose: () => void;
}

export default function SlashMenu({ query, onPick, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const filtered = SLASH_COMMANDS.filter((c) =>
    (c.label + c.hint).toLowerCase().includes(query.toLowerCase()),
  );

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [onClose]);

  if (filtered.length === 0) return null;

  return (
    <div
      ref={ref}
      className="absolute bottom-full left-0 right-0 z-30 mb-2 max-h-[280px] overflow-y-auto rounded-xl border border-border bg-popover p-1.5 shadow-xl"
    >
      <p className="px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        Slash 命令
      </p>
      {filtered.map((c) => (
        <button
          key={c.id}
          onClick={() => onPick(c)}
          className="flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left transition-colors hover:bg-accent"
        >
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <c.icon className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">{c.label}</p>
            <p className="truncate text-xs text-muted-foreground">{c.hint}</p>
          </div>
        </button>
      ))}
    </div>
  );
}
