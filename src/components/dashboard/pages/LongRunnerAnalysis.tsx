import { Clock, Timer, AlertCircle } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from "recharts";

const trendData = [
  { month: "10月", avg: 12, max: 45 },
  { month: "11月", avg: 14, max: 52 },
  { month: "12月", avg: 11, max: 38 },
  { month: "1月", avg: 16, max: 61 },
  { month: "2月", avg: 13, max: 48 },
  { month: "3月", avg: 15, max: 55 },
];

const longItems = [
  { id: "DEF-1089", title: "HMI 启动黑屏问题", days: 62, project: "MEB-Platform", status: "In Analysis" },
  { id: "DEF-0874", title: "CAN 信号丢失", days: 55, project: "PPE-Platform", status: "In Progress" },
  { id: "DEF-1203", title: "OTA 升级回滚失败", days: 48, project: "SSP-Platform", status: "In Analysis" },
  { id: "DEF-0951", title: "电池 SOC 计算偏差", days: 43, project: "MEB-Platform", status: "In Testing" },
  { id: "DEF-1105", title: "ADAS 摄像头标定异常", days: 38, project: "MLB-Evo", status: "In Progress" },
  { id: "DEF-0792", title: "座椅记忆功能失效", days: 35, project: "J1-Platform", status: "In Testing" },
];

const distData = [
  { range: "0-7天", count: 245 },
  { range: "8-14天", count: 178 },
  { range: "15-30天", count: 96 },
  { range: "31-60天", count: 42 },
  { range: "60天+", count: 18 },
];

const kpis = [
  { label: "平均解决周期", value: "13.5天", icon: Clock, color: "bg-primary/10 text-primary" },
  { label: "超期缺陷", value: "60", icon: Timer, color: "bg-warning/10 text-warning" },
  { label: "严重超期", value: "18", icon: AlertCircle, color: "bg-destructive/10 text-destructive" },
];

const LongRunnerAnalysis = () => (
  <div className="space-y-5">
    <div className="grid grid-cols-3 gap-4">
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
        <h3 className="mb-4 text-sm font-semibold text-foreground">解决周期趋势</h3>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" />
              <XAxis dataKey="month" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
              <Line type="monotone" dataKey="avg" name="平均天数" stroke="hsl(215, 70%, 48%)" strokeWidth={2} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="max" name="最大天数" stroke="hsl(0, 72%, 51%)" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="5 5" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="dashboard-card p-5">
        <h3 className="mb-4 text-sm font-semibold text-foreground">周期分布</h3>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={distData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" />
              <XAxis dataKey="range" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
              <Bar dataKey="count" name="缺陷数" fill="hsl(215, 70%, 48%)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>

    <div className="dashboard-card">
      <div className="border-b border-border px-5 py-4">
        <h3 className="text-sm font-semibold text-foreground">长周期缺陷列表</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left">
              <th className="px-5 py-3 font-medium text-muted-foreground">缺陷ID</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">标题</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">持续天数</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">项目</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">状态</th>
            </tr>
          </thead>
          <tbody>
            {longItems.map((item) => (
              <tr key={item.id} className="border-b border-border/50 last:border-0 hover:bg-muted/30 transition-colors">
                <td className="px-5 py-3 font-mono text-primary">{item.id}</td>
                <td className="px-5 py-3 font-medium text-foreground">{item.title}</td>
                <td className="px-5 py-3">
                  <span className={`font-mono ${item.days > 50 ? "text-destructive" : item.days > 30 ? "text-warning" : "text-foreground"}`}>
                    {item.days}天
                  </span>
                </td>
                <td className="px-5 py-3 text-muted-foreground">{item.project}</td>
                <td className="px-5 py-3">
                  <span className="inline-flex rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">{item.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  </div>
);

export default LongRunnerAnalysis;
