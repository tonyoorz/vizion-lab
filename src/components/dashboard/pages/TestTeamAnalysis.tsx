import { Users, Award, Target, Zap } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis } from "recharts";

const teamData = [
  { name: "张三", found: 68, closed: 52, efficiency: 76 },
  { name: "李四", found: 54, closed: 45, efficiency: 83 },
  { name: "王五", found: 47, closed: 38, efficiency: 81 },
  { name: "赵六", found: 42, closed: 35, efficiency: 83 },
  { name: "陈七", found: 38, closed: 31, efficiency: 82 },
  { name: "刘八", found: 35, closed: 28, efficiency: 80 },
];

const radarData = [
  { metric: "发现率", A: 85, B: 72 },
  { metric: "关闭率", A: 78, B: 80 },
  { metric: "响应速度", A: 92, B: 68 },
  { metric: "覆盖度", A: 70, B: 85 },
  { metric: "准确率", A: 88, B: 76 },
  { metric: "协作度", A: 75, B: 82 },
];

const kpis = [
  { label: "团队成员", value: "16", icon: Users, color: "bg-primary/10 text-primary" },
  { label: "人均发现", value: "24.5", icon: Target, color: "bg-success/10 text-success" },
  { label: "最佳效率", value: "83%", icon: Award, color: "bg-warning/10 text-warning" },
  { label: "团队效率", value: "79%", icon: Zap, color: "bg-accent/10 text-accent" },
];

const TestTeamAnalysis = () => (
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

    <div className="grid gap-5 lg:grid-cols-5">
      <div className="dashboard-card p-5 lg:col-span-3">
        <h3 className="mb-4 text-sm font-semibold text-foreground">团队成员缺陷处理量</h3>
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={teamData} barGap={4}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
              <Bar dataKey="found" name="发现" fill="hsl(215, 70%, 48%)" radius={[4, 4, 0, 0]} />
              <Bar dataKey="closed" name="关闭" fill="hsl(152, 60%, 40%)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="dashboard-card p-5 lg:col-span-2">
        <h3 className="mb-4 text-sm font-semibold text-foreground">团队能力雷达图</h3>
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={radarData}>
              <PolarGrid stroke="hsl(220, 16%, 90%)" />
              <PolarAngleAxis dataKey="metric" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <PolarRadiusAxis tick={{ fontSize: 10 }} />
              <Radar name="A组" dataKey="A" stroke="hsl(215, 70%, 48%)" fill="hsl(215, 70%, 48%)" fillOpacity={0.2} />
              <Radar name="B组" dataKey="B" stroke="hsl(152, 60%, 40%)" fill="hsl(152, 60%, 40%)" fillOpacity={0.2} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>

    <div className="dashboard-card">
      <div className="border-b border-border px-5 py-4">
        <h3 className="text-sm font-semibold text-foreground">成员绩效明细</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left">
              <th className="px-5 py-3 font-medium text-muted-foreground">成员</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">发现数</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">关闭数</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">效率</th>
            </tr>
          </thead>
          <tbody>
            {teamData.map((t) => (
              <tr key={t.name} className="border-b border-border/50 last:border-0 hover:bg-muted/30 transition-colors">
                <td className="px-5 py-3 font-medium text-foreground">{t.name}</td>
                <td className="px-5 py-3 font-mono text-foreground">{t.found}</td>
                <td className="px-5 py-3 font-mono text-success">{t.closed}</td>
                <td className="px-5 py-3">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-16 rounded-full bg-muted">
                      <div className="h-full rounded-full bg-primary" style={{ width: `${t.efficiency}%` }} />
                    </div>
                    <span className="text-xs text-muted-foreground">{t.efficiency}%</span>
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

export default TestTeamAnalysis;
