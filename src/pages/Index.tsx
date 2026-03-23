import { useState } from "react";
import DashboardSidebar from "@/components/dashboard/DashboardSidebar";
import FilterPanel from "@/components/dashboard/FilterPanel";
import KPICards from "@/components/dashboard/KPICards";
import DefectTrendChart from "@/components/dashboard/DefectTrendChart";
import StatusDistributionChart from "@/components/dashboard/StatusDistributionChart";
import TopIssueTable from "@/components/dashboard/TopIssueTable";
import AIAssistant from "@/components/dashboard/AIAssistant";

const Index = () => {
  const [activeNav, setActiveNav] = useState("topissue");

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <DashboardSidebar active={activeNav} onNavigate={setActiveNav} />

      <main className="flex-1 overflow-y-auto">
        {/* Top bar */}
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-border bg-background/80 backdrop-blur-md px-6 py-3.5">
          <div>
            <h1 className="text-lg font-bold text-foreground">DTSV 测试与缺陷数据分析</h1>
            <p className="text-xs text-muted-foreground">Testing and Defect Data Analysis Dashboard</p>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="flex h-2 w-2 rounded-full bg-success" />
            数据已同步 · 2026-03-23
          </div>
        </header>

        {/* Content */}
        <div className="space-y-5 p-6">
          <FilterPanel />
          <KPICards />
          <div className="grid gap-5 lg:grid-cols-5">
            <div className="lg:col-span-3">
              <DefectTrendChart />
            </div>
            <div className="lg:col-span-2">
              <StatusDistributionChart />
            </div>
          </div>
          <TopIssueTable />
        </div>
      </main>

      <AIAssistant />
    </div>
  );
};

export default Index;
