import { ListChecks, PlayCircle, PauseCircle, CheckCircle2 } from "lucide-react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid } from "recharts";

const statusPie = [
  { name: "通过", value: 2145, color: "hsl(152, 60%, 40%)" },
  { name: "失败", value: 312, color: "hsl(0, 72%, 51%)" },
  { name: "阻塞", value: 186, color: "hsl(38, 92%, 50%)" },
  { name: "未执行", value: 1204, color: "hsl(220, 16%, 80%)" },
];

const weeklyData = [
  { week: "W10", passed: 180, failed: 22, blocked: 8 },
  { week: "W11", passed: 210, failed: 28, blocked: 12 },
  { week: "W12", passed: 195, failed: 18, blocked: 6 },
  { week: "W13", passed: 240, failed: 35, blocked: 15 },
  { week: "W14", passed: 260, failed: 20, blocked: 10 },
  { week: "W15", passed: 230, failed: 25, blocked: 9 },
];

const kpis = [
  { label: "用例总数", value: "3,847", icon: ListChecks, color: "bg-primary/10 text-primary" },
  { label: "执行中", value: "428", icon: PlayCircle, color: "bg-warning/10 text-warning" },
  { label: "阻塞", value: "186", icon: PauseCircle, color: "bg-destructive/10 text-destructive" },
  { label: "通过率", value: "87.3%", icon: CheckCircle2, color: "bg-success/10 text-success" },
];

const TestStatusAnalysis = () => (
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
      <div className="dashboard-card p-5 lg:col-span-2">
        <h3 className="mb-4 text-sm font-semibold text-foreground">测试状态分布</h3>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={statusPie} cx="50%" cy="50%" innerRadius={55} outerRadius={95} paddingAngle={3} dataKey="value">
                {statusPie.map((entry) => (
                  <Cell key={entry.name} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-wrap justify-center gap-3 mt-2">
          {statusPie.map((s) => (
            <div key={s.name} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
              {s.name} ({s.value})
            </div>
          ))}
        </div>
      </div>

      <div className="dashboard-card p-5 lg:col-span-3">
        <h3 className="mb-4 text-sm font-semibold text-foreground">每周执行趋势</h3>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={weeklyData} barGap={2}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" />
              <XAxis dataKey="week" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
              <Bar dataKey="passed" name="通过" stackId="a" fill="hsl(152, 60%, 40%)" />
              <Bar dataKey="failed" name="失败" stackId="a" fill="hsl(0, 72%, 51%)" />
              <Bar dataKey="blocked" name="阻塞" stackId="a" fill="hsl(38, 92%, 50%)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  </div>
);

export default TestStatusAnalysis;
