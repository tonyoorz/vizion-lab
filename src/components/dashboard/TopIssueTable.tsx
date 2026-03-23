import { Badge } from "@/components/ui/badge";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";

const issues = [
  { id: "DEF-1024", title: "ESP 传感器信号间歇性丢失", severity: "Showstopper", project: "BR513", status: "In Analysis", age: 45 },
  { id: "DEF-0987", title: "BCM 模块通信超时", severity: "Critical", project: "CD762", status: "In Progress", age: 32 },
  { id: "DEF-1102", title: "HMI 显示异常 - 仪表盘黑屏", severity: "Showstopper", project: "BR513", status: "In Testing", age: 28 },
  { id: "DEF-0856", title: "ADAS 摄像头标定偏移", severity: "Critical", project: "EV401", status: "In Analysis", age: 21 },
  { id: "DEF-1198", title: "OTA 升级中断后无法恢复", severity: "Critical", project: "CD762", status: "New", age: 14 },
  { id: "DEF-0934", title: "电池管理系统温度阈值报警", severity: "Major", project: "EV401", status: "In Progress", age: 12 },
  { id: "DEF-1056", title: "车窗防夹功能失效", severity: "Showstopper", project: "BR513", status: "In Testing", age: 8 },
];

const severityStyles: Record<string, string> = {
  Showstopper: "bg-destructive/10 text-destructive border-0",
  Critical: "bg-warning/10 text-warning border-0",
  Major: "bg-primary/10 text-primary border-0",
};

const statusStyles: Record<string, string> = {
  "In Analysis": "bg-muted text-muted-foreground",
  "In Progress": "bg-primary/10 text-primary",
  "In Testing": "bg-success/10 text-success",
  New: "bg-warning/10 text-warning",
};

const TopIssueTable = () => {
  return (
    <div className="dashboard-card">
      <div className="flex items-center justify-between border-b border-border px-5 py-4">
        <div>
          <h3 className="text-base font-semibold text-foreground">Top Issue 列表</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">按严重程度和周期排序的关键问题</p>
        </div>
        <Button variant="outline" size="sm" className="gap-2">
          <Download className="h-4 w-4" />
          导出 Excel
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border text-left">
              <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">ID</th>
              <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">问题描述</th>
              <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">严重级别</th>
              <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">项目</th>
              <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">状态</th>
              <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground text-right">天数</th>
            </tr>
          </thead>
          <tbody>
            {issues.map((issue, i) => (
              <tr
                key={issue.id}
                className="border-b border-border/50 transition-colors hover:bg-muted/30 animate-fade-in"
                style={{ animationDelay: `${i * 50}ms` }}
              >
                <td className="px-5 py-3.5 text-sm font-mono font-medium text-primary">{issue.id}</td>
                <td className="px-5 py-3.5 text-sm text-foreground">{issue.title}</td>
                <td className="px-5 py-3.5">
                  <Badge className={severityStyles[issue.severity]}>{issue.severity}</Badge>
                </td>
                <td className="px-5 py-3.5 text-sm text-muted-foreground">{issue.project}</td>
                <td className="px-5 py-3.5">
                  <Badge variant="secondary" className={statusStyles[issue.status]}>{issue.status}</Badge>
                </td>
                <td className="px-5 py-3.5 text-right">
                  <span className={`text-sm font-semibold ${issue.age > 30 ? "text-destructive" : issue.age > 14 ? "text-warning" : "text-foreground"}`}>
                    {issue.age}d
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default TopIssueTable;
