import { BarChart3, CheckCircle2, Clock, XCircle } from "lucide-react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid } from "recharts";

const statusPie = [
  { name: "New", value: 128, color: "hsl(215, 70%, 48%)" },
  { name: "In Analysis", value: 96, color: "hsl(38, 92%, 50%)" },
  { name: "In Progress", value: 188, color: "hsl(280, 60%, 55%)" },
  { name: "In Testing", value: 145, color: "hsl(180, 50%, 45%)" },
  { name: "Closed", value: 847, color: "hsl(152, 60%, 40%)" },
  { name: "Rejected", value: 42, color: "hsl(220, 16%, 70%)" },
];

const flowData = [
  { month: "10月", new: 85, closed: 62, inProgress: 120 },
  { month: "11月", new: 92, closed: 78, inProgress: 134 },
  { month: "12月", new: 68, closed: 88, inProgress: 114 },
  { month: "1月", new: 105, closed: 95, inProgress: 124 },
  { month: "2月", new: 78, closed: 102, inProgress: 100 },
  { month: "3月", new: 72, closed: 110, inProgress: 88 },
];

const kpis = [
  { label: "总缺陷", value: "1,446", icon: BarChart3, color: "bg-primary/10 text-primary" },
  { label: "已关闭", value: "847", icon: CheckCircle2, color: "bg-success/10 text-success" },
  { label: "处理中", value: "557", icon: Clock, color: "bg-warning/10 text-warning" },
  { label: "已拒绝", value: "42", icon: XCircle, color: "bg-muted text-muted-foreground" },
];

const DefectStatusAnalysis = () => (
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
        <h3 className="mb-4 text-sm font-semibold text-foreground">缺陷状态分布</h3>
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={statusPie} cx="50%" cy="50%" innerRadius={55} outerRadius={100} paddingAngle={2} dataKey="value">
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
              {s.name}
            </div>
          ))}
        </div>
      </div>

      <div className="dashboard-card p-5 lg:col-span-3">
        <h3 className="mb-4 text-sm font-semibold text-foreground">缺陷流转趋势</h3>
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={flowData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" />
              <XAxis dataKey="month" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
              <Area type="monotone" dataKey="new" name="新增" stroke="hsl(215, 70%, 48%)" fill="hsl(215, 70%, 48%)" fillOpacity={0.1} strokeWidth={2} />
              <Area type="monotone" dataKey="closed" name="关闭" stroke="hsl(152, 60%, 40%)" fill="hsl(152, 60%, 40%)" fillOpacity={0.1} strokeWidth={2} />
              <Area type="monotone" dataKey="inProgress" name="处理中" stroke="hsl(38, 92%, 50%)" fill="hsl(38, 92%, 50%)" fillOpacity={0.1} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  </div>
);

export default DefectStatusAnalysis;
