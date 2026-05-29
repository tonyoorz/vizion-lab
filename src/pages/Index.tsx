import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import DashboardHeader from "@/components/dashboard/DashboardHeader";
import DashboardSidebar from "@/components/dashboard/DashboardSidebar";
import FilterPanel from "@/components/dashboard/FilterPanel";
import KPICards from "@/components/dashboard/KPICards";
import DefectTrendChart from "@/components/dashboard/DefectTrendChart";
import StatusDistributionChart from "@/components/dashboard/StatusDistributionChart";
import TopIssueTable from "@/components/dashboard/TopIssueTable";
import AIAssistant from "@/components/dashboard/AIAssistant";
import ProjectAnalysis from "@/components/dashboard/pages/ProjectAnalysis";
import DefectHighFreq from "@/components/dashboard/pages/DefectHighFreq";
import LongRunnerAnalysis from "@/components/dashboard/pages/LongRunnerAnalysis";
import TestTeamAnalysis from "@/components/dashboard/pages/TestTeamAnalysis";
import CoverageAnalysis from "@/components/dashboard/pages/CoverageAnalysis";
import TestStatusAnalysis from "@/components/dashboard/pages/TestStatusAnalysis";
import DefectStatusAnalysis from "@/components/dashboard/pages/DefectStatusAnalysis";
import AIChat from "@/components/dashboard/pages/AIChat";

const pageTitles: Record<string, { title: string; subtitle: string }> = {
  topissue: { title: "Top Issue 分析", subtitle: "关键问题追踪与趋势分析" },
  project: { title: "项目分析", subtitle: "多项目缺陷对比与进度总览" },
  "defect-high": { title: "缺陷高频分析", subtitle: "高频缺陷模块识别与根因定位" },
  "long-runner": { title: "长周期分析", subtitle: "超期缺陷追踪与周期优化" },
  "test-team": { title: "测试团队分析", subtitle: "团队绩效与能力评估" },
  coverage: { title: "测试覆盖率分析", subtitle: "模块覆盖率监控与提升" },
  "test-status": { title: "测试状态分析", subtitle: "用例执行状态与通过率" },
  "defect-status": { title: "缺陷状态分析", subtitle: "缺陷生命周期与流转趋势" },
  "ai-chat": { title: "AI Chat", subtitle: "与 DTSV 智能体对话，获取分析洞察" },
};

const Index = () => {
  const [activeNav, setActiveNav] = useState("topissue");
  const info = pageTitles[activeNav] || pageTitles.topissue;

  const renderContent = () => {
    switch (activeNav) {
      case "project":
        return <ProjectAnalysis />;
      case "defect-high":
        return <DefectHighFreq />;
      case "long-runner":
        return <LongRunnerAnalysis />;
      case "test-team":
        return <TestTeamAnalysis />;
      case "coverage":
        return <CoverageAnalysis />;
      case "test-status":
        return <TestStatusAnalysis />;
      case "defect-status":
        return <DefectStatusAnalysis />;
      case "ai-chat":
        return <AIChat moduleKey={activeNav} moduleLabel={info.title} />;
      default:
        return (
          <>
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
          </>
        );
    }
  };

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <DashboardSidebar active={activeNav} onNavigate={setActiveNav} />

      <main className="flex-1 overflow-y-auto">
        <DashboardHeader title={info.title} subtitle={info.subtitle} />

        <div className="space-y-5 p-6">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeNav}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25, ease: "easeInOut" }}
            >
              {renderContent()}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>

      <AIAssistant />
    </div>
  );
};

export default Index;
