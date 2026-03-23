import { ShieldCheck, FileCheck, Layers, BarChart3 } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from "recharts";

const coverageByModule = [
  { module: "HMI", coverage: 87 },
  { module: "动力总成", coverage: 92 },
  { module: "底盘", coverage: 78 },
  { module: "ADAS", coverage: 71 },
  { module: "车身电子", coverage: 85 },
  { module: "信息娱乐", coverage: 69 },
  { module: "网络通信", coverage: 83 },
];

const trendData = [
  { month: "10月", coverage: 72 },
  { month: "11月", coverage: 75 },
  { month: "12月", coverage: 74 },
  { month: "1月", coverage: 78 },
  { month: "2月", coverage: 81 },
  { month: "3月", coverage: 82 },
];

const kpis = [
  { label: "总覆盖率", value: "82%", icon: ShieldCheck, color: "bg-success/10 text-success" },
  { label: "用例总数", value: "3,847", icon: FileCheck, color: "bg-primary/10 text-primary" },
  { label: "覆盖模块", value: "24", icon: Layers, color: "bg-warning/10 text-warning" },
  { label: "月增长", value: "+1.2%", icon: BarChart3, color: "bg-accent/10 text-accent" },
];

const CoverageAnalysis = () => (
  <div className="space-y-5">
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {kpis.map((k) => {
        const Icon = k.icon;
        return (
          <div key={k.label} className="dashboard-card p-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="kpi-label">{k.label}</p>
                <p className="kpi-value mt-1">{k.value}</p>
              </div>
              <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${k.color}`}>
                <Icon className="h-5 w-5" />
              </div>
            </div>
          </div>
        );
      })}
    </div>

    <div className="grid gap-5 lg:grid-cols-2">
      <div className="dashboard-card p-5">
        <h3 className="mb-4 text-sm font-semibold text-foreground">各模块覆盖率</h3>
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={coverageByModule} layout="vertical" barSize={16}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" horizontal={false} />
              <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis dataKey="module" type="category" width={80} tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} formatter={(v: number) => `${v}%`} />
              <Bar dataKey="coverage" name="覆盖率" fill="hsl(152, 60%, 40%)" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="dashboard-card p-5">
        <h3 className="mb-4 text-sm font-semibold text-foreground">覆盖率趋势</h3>
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" />
              <XAxis dataKey="month" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis domain={[60, 100]} tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} formatter={(v: number) => `${v}%`} />
              <Area type="monotone" dataKey="coverage" name="覆盖率" stroke="hsl(152, 60%, 40%)" fill="hsl(152, 60%, 40%)" fillOpacity={0.15} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>

    <div className="dashboard-card">
      <div className="border-b border-border px-5 py-4">
        <h3 className="text-sm font-semibold text-foreground">模块覆盖详情</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left">
              <th className="px-5 py-3 font-medium text-muted-foreground">模块</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">覆盖率</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">进度</th>
            </tr>
          </thead>
          <tbody>
            {coverageByModule.map((m) => (
              <tr key={m.module} className="border-b border-border/50 last:border-0 hover:bg-muted/30 transition-colors">
                <td className="px-5 py-3 font-medium text-foreground">{m.module}</td>
                <td className="px-5 py-3 font-mono text-foreground">{m.coverage}%</td>
                <td className="px-5 py-3">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-24 rounded-full bg-muted">
                      <div
                        className={`h-full rounded-full ${m.coverage >= 80 ? "bg-success" : m.coverage >= 70 ? "bg-warning" : "bg-destructive"}`}
                        style={{ width: `${m.coverage}%` }}
                      />
                    </div>
                    <span className="text-xs text-muted-foreground">{m.coverage}%</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  </div>
);

export default CoverageAnalysis;
