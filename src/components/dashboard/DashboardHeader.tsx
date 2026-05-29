import { useState, useEffect } from "react";
import { Search, Bell, Moon, Sun, ChevronRight } from "lucide-react";

interface DashboardHeaderProps {
  title: string;
  subtitle: string;
}

const DashboardHeader = ({ title, subtitle }: DashboardHeaderProps) => {
  const [isDark, setIsDark] = useState(() =>
    typeof document !== "undefined"
      ? document.documentElement.classList.contains("dark")
      : false
  );

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.classList.contains("dark"));
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);

  const toggleDark = () => {
    document.documentElement.classList.toggle("dark");
    setIsDark(document.documentElement.classList.contains("dark"));
  };

  return (
    <header className="sticky top-0 z-30 flex items-center justify-between border-b border-border bg-background/80 backdrop-blur-md px-6 py-3.5">
      {/* Left: Breadcrumb */}
      <div className="flex flex-col gap-0.5 min-w-0">
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <span className="font-medium">DTSV</span>
          <ChevronRight className="h-3 w-3" />
          <span className="text-foreground font-medium truncate">{title}</span>
        </div>
        <p className="text-[11px] text-muted-foreground truncate">{subtitle}</p>
      </div>

      {/* Center: Global Search trigger */}
      <button
        type="button"
        className="hidden md:flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted transition-colors w-64 cursor-default"
      >
        <Search className="h-3.5 w-3.5" />
        <span className="flex-1 text-left">搜索...</span>
        <kbd className="pointer-events-none inline-flex h-5 items-center rounded border border-border bg-background px-1.5 text-[10px] font-mono text-muted-foreground">
          ⌘K
        </kbd>
      </button>

      {/* Right: Actions */}
      <div className="flex items-center gap-3">
        {/* Notification Bell */}
        <button
          type="button"
          className="relative rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          aria-label="通知"
        >
          <Bell className="h-4 w-4" />
          <span className="absolute top-0.5 right-0.5 flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
          </span>
        </button>

        {/* Dark Mode Toggle */}
        <button
          type="button"
          onClick={toggleDark}
          className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          aria-label={isDark ? "切换亮色模式" : "切换暗色模式"}
        >
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>

        {/* Sync Status */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="flex h-2 w-2 rounded-full bg-success" />
          数据已同步 · 2026-03-23
        </div>
      </div>
    </header>
  );
};

export default DashboardHeader;
