import { useState, useEffect } from "react";
import {
  BarChart3,
  Bug,
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  LayoutDashboard,
  LineChart,
  ListChecks,
  Moon,
  Settings,
  ShieldCheck,
  Sparkles,
  Sun,
  TrendingUp,
  Users,
} from "lucide-react";

interface NavItem {
  id: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
}

interface NavGroup {
  header: string;
  items: NavItem[];
}

const navGroups: NavGroup[] = [
  {
    header: "缺陷分析",
    items: [
      { id: "topissue", label: "Top Issue 分析", icon: TrendingUp },
      { id: "project", label: "项目分析", icon: LayoutDashboard },
      { id: "defect-high", label: "缺陷高频分析", icon: Bug },
      { id: "long-runner", label: "长周期分析", icon: LineChart },
    ],
  },
  {
    header: "测试分析",
    items: [
      { id: "test-team", label: "测试团队分析", icon: Users },
      { id: "coverage", label: "测试覆盖率分析", icon: ShieldCheck },
      { id: "test-status", label: "测试状态分析", icon: ListChecks },
      { id: "defect-status", label: "缺陷状态分析", icon: BarChart3 },
    ],
  },
  {
    header: "智能助手",
    items: [
      { id: "ai-chat", label: "AI Chat", icon: Sparkles, badge: "Beta" },
    ],
  },
];

interface DashboardSidebarProps {
  active: string;
  onNavigate: (id: string) => void;
}

const DashboardSidebar = ({ active, onNavigate }: DashboardSidebarProps) => {
  const [collapsed, setCollapsed] = useState(false);
  const [isDark, setIsDark] = useState(() =>
    typeof document !== "undefined"
      ? document.documentElement.classList.contains("dark")
      : true
  );

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggleDarkMode = () => {
    document.documentElement.classList.toggle("dark");
    setIsDark((prev) => !prev);
  };

  return (
    <aside
      className={`relative flex flex-col bg-[hsl(var(--sidebar-bg))] transition-all duration-300 ${
        collapsed ? "w-[68px]" : "w-[240px]"
      }`}
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-6">
        <div className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary overflow-hidden">
          {/* Glow overlay */}
          <div className="absolute inset-0 bg-gradient-to-br from-white/20 via-transparent to-transparent" />
          <FlaskConical className="relative z-10 h-5 w-5 text-primary-foreground" />
        </div>
        {!collapsed && (
          <div className="overflow-hidden">
            <h1 className="truncate text-sm font-bold text-[hsl(0,0%,95%)]">
              DTSV
            </h1>
            <p className="truncate text-xs text-[hsl(var(--sidebar-fg))]">
              数据分析平台
            </p>
          </div>
        )}
      </div>

      {/* Nav Groups */}
      <nav className="flex-1 space-y-4 overflow-y-auto px-3">
        {navGroups.map((group, groupIndex) => (
          <div key={group.header}>
            {/* Group Header */}
            {!collapsed && (
              <div
                className={`px-3 text-[10px] font-semibold uppercase tracking-widest text-[hsl(var(--sidebar-fg)/0.5)] ${
                  groupIndex === 0 ? "pt-0" : "pt-1"
                } pb-1.5`}
              >
                {group.header}
              </div>
            )}
            {/* Collapsed separator for groups after the first */}
            {collapsed && groupIndex > 0 && (
              <div className="mx-2 my-2 border-t border-[hsl(var(--sidebar-border))]" />
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const isActive = active === item.id;
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    onClick={() => onNavigate(item.id)}
                    className={`nav-item relative w-full ${
                      isActive
                        ? "bg-primary/20 text-primary"
                        : "nav-item-inactive"
                    }`}
                    title={collapsed ? item.label : undefined}
                  >
                    {/* Active indicator bar */}
                    {isActive && (
                      <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-full bg-primary" />
                    )}
                    <Icon className="h-[18px] w-[18px] shrink-0" />
                    {!collapsed && (
                      <>
                        <span className="flex-1 truncate text-left">
                          {item.label}
                        </span>
                        {item.badge && (
                          <span className="rounded-md bg-primary/20 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-primary">
                            {item.badge}
                          </span>
                        )}
                      </>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Bottom User Section */}
      <div className="border-t border-[hsl(var(--sidebar-border))] px-3 py-3">
        <div className="flex items-center gap-3">
          {/* Avatar */}
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/20 text-xs font-bold text-primary">
            {collapsed ? "T" : "TH"}
          </div>
          {!collapsed && (
            <div className="flex-1 overflow-hidden">
              <p className="truncate text-sm font-medium text-[hsl(0,0%,95%)]">
                Tony
              </p>
              <p className="truncate text-[11px] text-[hsl(var(--sidebar-fg)/0.6)]">
                管理员
              </p>
            </div>
          )}
          {/* Action buttons */}
          {!collapsed && (
            <div className="flex items-center gap-0.5">
              <button
                onClick={toggleDarkMode}
                className="flex h-7 w-7 items-center justify-center rounded-md text-[hsl(var(--sidebar-fg)/0.6)] transition-colors hover:bg-[hsl(var(--sidebar-hover))] hover:text-[hsl(0,0%,95%)]"
                title={isDark ? "切换浅色模式" : "切换深色模式"}
              >
                {isDark ? (
                  <Sun className="h-4 w-4" />
                ) : (
                  <Moon className="h-4 w-4" />
                )}
              </button>
              <button
                className="flex h-7 w-7 items-center justify-center rounded-md text-[hsl(var(--sidebar-fg)/0.6)] transition-colors hover:bg-[hsl(var(--sidebar-hover))] hover:text-[hsl(0,0%,95%)]"
                title="设置"
              >
                <Settings className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="absolute -right-3 top-20 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-card shadow-sm hover:bg-secondary transition-colors"
      >
        {collapsed ? (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronLeft className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>
    </aside>
  );
};

export default DashboardSidebar;
