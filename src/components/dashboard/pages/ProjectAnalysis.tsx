import { BarChart3, FolderOpen, GitBranch, Users } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from "recharts";

const projectData = [
  { name: "MEB-Platform", defects: 186, resolved: 142, pending: 44 },
  { name: "PPE-Platform", defects: 154, resolved: 118, pending: 36 },
  { name: "MLB-Evo", defects: 128, resolved: 95, pending: 33 },
  { name: "SSP-Platform", defects: 97, resolved: 72, pending: 25 },
  { name: "J1-Platform", defects: 84, resolved: 61, pending: 23 },
  { name: "MSB-Platform", defects: 65, resolved: 52, pending: 13 },
];

const statusData = [
  { name: "按时完成", value: 62, color: "hsl(152, 60%, 40%)" },
  { name: "延期完成", value: 23, color: "hsl(38, 92%, 50%)" },
  { name: "进行中", value: 15, color: "hsl(215, 70%, 48%)" },
];

const kpis = [
  { label: "活跃项目", value: "12", icon: FolderOpen, color: "bg-primary/10 text-primary" },
  { label: "参与团队", value: "8", icon: Users, color: "bg-success/10 text-success" },
  { label: "版本迭代", value: "47", icon: GitBranch, color: "bg-warning/10 text-warning" },
  { label: "完成率", value: "78%", icon: BarChart3, color: "bg-accent/10 text-accent" },
];

const ProjectAnalysis = () => (
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
        <h3 className="mb-4 text-sm font-semibold text-foreground">各项目缺陷数量对比</h3>
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={projectData} barGap={4}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
              <Bar dataKey="resolved" name="已解决" fill="hsl(152, 60%, 40%)" radius={[4, 4, 0, 0]} />
              <Bar dataKey="pending" name="待处理" fill="hsl(38, 92%, 50%)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="dashboard-card p-5 lg:col-span-2">
        <h3 className="mb-4 text-sm font-semibold text-foreground">项目进度分布</h3>
        <div className="h-[320px] flex items-center justify-center">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={statusData} cx="50%" cy="50%" innerRadius={60} outerRadius={100} paddingAngle={3} dataKey="value">
                {statusData.map((entry) => (
                  <Cell key={entry.name} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-2 flex justify-center gap-4">
          {statusData.map((s) => (
            <div key={s.name} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
              {s.name} ({s.value}%)
            </div>
          ))}
        </div>
      </div>
    </div>

    <div className="dashboard-card">
      <div className="border-b border-border px-5 py-4">
        <h3 className="text-sm font-semibold text-foreground">项目详情</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left">
              <th className="px-5 py-3 font-medium text-muted-foreground">项目名称</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">总缺陷</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">已解决</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">待处理</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">解决率</th>
            </tr>
          </thead>
          <tbody>
            {projectData.map((p) => (
              <tr key={p.name} className="border-b border-border/50 last:border-0 hover:bg-muted/30 transition-colors">
                <td className="px-5 py-3 font-medium text-foreground">{p.name}</td>
                <td className="px-5 py-3 font-mono text-foreground">{p.defects}</td>
                <td className="px-5 py-3 font-mono text-success">{p.resolved}</td>
                <td className="px-5 py-3 font-mono text-warning">{p.pending}</td>
                <td className="px-5 py-3">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-20 rounded-full bg-muted">
                      <div className="h-full rounded-full bg-success" style={{ width: `${Math.round((p.resolved / p.defects) * 100)}%` }} />
                    </div>
                    <span className="text-xs text-muted-foreground">{Math.round((p.resolved / p.defects) * 100)}%</span>
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

export default ProjectAnalysis;
