import { AlertTriangle, Repeat, TrendingUp } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ScatterChart, Scatter, ZAxis } from "recharts";

const freqData = [
  { module: "HMI Display", count: 42, severity: "Critical" },
  { module: "CAN通信", count: 38, severity: "High" },
  { module: "OTA升级", count: 31, severity: "High" },
  { module: "电池管理", count: 28, severity: "Critical" },
  { module: "ADAS传感器", count: 25, severity: "Medium" },
  { module: "座椅控制", count: 19, severity: "Low" },
  { module: "空调系统", count: 16, severity: "Medium" },
  { module: "车灯控制", count: 12, severity: "Low" },
];

const repeatData = [
  { x: 1, y: 42, z: 8, name: "HMI Display" },
  { x: 2, y: 38, z: 6, name: "CAN通信" },
  { x: 3, y: 31, z: 5, name: "OTA升级" },
  { x: 4, y: 28, z: 7, name: "电池管理" },
  { x: 5, y: 25, z: 3, name: "ADAS传感器" },
  { x: 6, y: 19, z: 2, name: "座椅控制" },
];

const severityColor: Record<string, string> = {
  Critical: "bg-destructive/10 text-destructive",
  High: "bg-warning/10 text-warning",
  Medium: "bg-primary/10 text-primary",
  Low: "bg-muted text-muted-foreground",
};

const kpis = [
  { label: "高频模块", value: "8", icon: Repeat, color: "bg-destructive/10 text-destructive" },
  { label: "重复出现率", value: "34%", icon: TrendingUp, color: "bg-warning/10 text-warning" },
  { label: "关键缺陷", value: "15", icon: AlertTriangle, color: "bg-primary/10 text-primary" },
];

const DefectHighFreq = () => (
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
        <h3 className="mb-4 text-sm font-semibold text-foreground">缺陷高频模块排名</h3>
        <div className="h-[340px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={freqData} layout="vertical" barSize={18}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis dataKey="module" type="category" width={90} tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
              <Bar dataKey="count" name="缺陷数" fill="hsl(0, 72%, 51%)" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="dashboard-card p-5">
        <h3 className="mb-4 text-sm font-semibold text-foreground">缺陷频次与重复率气泡图</h3>
        <div className="h-[340px]">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 90%)" />
              <XAxis dataKey="x" name="模块" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <YAxis dataKey="y" name="缺陷数" tick={{ fontSize: 11, fill: "hsl(220, 10%, 50%)" }} />
              <ZAxis dataKey="z" range={[100, 600]} name="重复次数" />
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid hsl(220,16%,90%)", fontSize: 12 }} />
              <Scatter data={repeatData} fill="hsl(215, 70%, 48%)" fillOpacity={0.7} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>

    <div className="dashboard-card">
      <div className="border-b border-border px-5 py-4">
        <h3 className="text-sm font-semibold text-foreground">高频缺陷详情</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left">
              <th className="px-5 py-3 font-medium text-muted-foreground">模块</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">缺陷数</th>
              <th className="px-5 py-3 font-medium text-muted-foreground">严重程度</th>
            </tr>
          </thead>
          <tbody>
            {freqData.map((d) => (
              <tr key={d.module} className="border-b border-border/50 last:border-0 hover:bg-muted/30 transition-colors">
                <td className="px-5 py-3 font-medium text-foreground">{d.module}</td>
                <td className="px-5 py-3 font-mono text-foreground">{d.count}</td>
                <td className="px-5 py-3">
                  <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ${severityColor[d.severity]}`}>
                    {d.severity}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  </div>
);

export default DefectHighFreq;
