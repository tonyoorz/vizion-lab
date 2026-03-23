import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

const data = [
  { name: "已关闭", value: 847, color: "hsl(152, 60%, 40%)" },
  { name: "进行中", value: 312, color: "hsl(215, 70%, 48%)" },
  { name: "待分析", value: 68, color: "hsl(38, 92%, 50%)" },
  { name: "关键", value: 57, color: "hsl(0, 72%, 51%)" },
];

const StatusDistributionChart = () => {
  const total = data.reduce((sum, d) => sum + d.value, 0);

  return (
    <div className="dashboard-card p-5">
      <div className="mb-4">
        <h3 className="text-base font-semibold text-foreground">状态分布</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">缺陷当前状态占比</p>
      </div>
      <div className="flex items-center gap-6">
        <ResponsiveContainer width={180} height={180}>
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={80}
              paddingAngle={3}
              dataKey="value"
              stroke="none"
            >
              {data.map((entry, index) => (
                <Cell key={index} fill={entry.color} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                background: "hsl(0, 0%, 100%)",
                border: "1px solid hsl(220, 16%, 90%)",
                borderRadius: "10px",
                boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                fontSize: "13px",
              }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="flex-1 space-y-3">
          {data.map((item) => (
            <div key={item.name} className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: item.color }}
                />
                <span className="text-sm text-foreground">{item.name}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-sm font-semibold text-foreground">{item.value}</span>
                <span className="text-xs text-muted-foreground w-10 text-right">
                  {((item.value / total) * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default StatusDistributionChart;
