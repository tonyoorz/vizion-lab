import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Bug, CheckCircle2, Clock, TrendingDown, TrendingUp } from "lucide-react";
import { Area, AreaChart } from "recharts";

const sparklineDataMap: Record<string, number[]> = {
  总缺陷数: [65, 78, 85, 72, 90, 95, 102, 88, 110, 120, 115, 128],
  已关闭: [40, 55, 60, 65, 70, 78, 82, 88, 90, 95, 100, 105],
  进行中: [50, 45, 48, 42, 55, 50, 45, 40, 38, 35, 32, 30],
  关键问题: [80, 90, 95, 100, 105, 110, 108, 112, 118, 120, 122, 125],
};

const kpis = [
  {
    label: "总缺陷数",
    value: 1284,
    change: "+12%",
    trend: "up" as const,
    icon: Bug,
    color: "primary",
  },
  {
    label: "已关闭",
    value: 847,
    change: "+8%",
    trend: "up" as const,
    icon: CheckCircle2,
    color: "success",
  },
  {
    label: "进行中",
    value: 312,
    change: "-5%",
    trend: "down" as const,
    icon: Clock,
    color: "warning",
  },
  {
    label: "关键问题",
    value: 125,
    change: "+3%",
    trend: "up" as const,
    icon: AlertTriangle,
    color: "destructive",
  },
];

const colorMap: Record<
  string,
  {
    bg: string;
    text: string;
    icon: string;
    stroke: string;
    fill: string;
    gradientFrom: string;
    gradientTo: string;
  }
> = {
  primary: {
    bg: "bg-primary/10",
    text: "text-primary",
    icon: "text-primary",
    stroke: "var(--color-primary, hsl(221, 83%, 53%))",
    fill: "var(--color-primary, hsl(221, 83%, 53%))",
    gradientFrom: "from-primary/80",
    gradientTo: "to-primary/30",
  },
  success: {
    bg: "bg-success/10",
    text: "text-success",
    icon: "text-success",
    stroke: "var(--color-success, hsl(142, 71%, 45%))",
    fill: "var(--color-success, hsl(142, 71%, 45%))",
    gradientFrom: "from-success/80",
    gradientTo: "to-success/30",
  },
  warning: {
    bg: "bg-warning/10",
    text: "text-warning",
    icon: "text-warning",
    stroke: "var(--color-warning, hsl(38, 92%, 50%))",
    fill: "var(--color-warning, hsl(38, 92%, 50%))",
    gradientFrom: "from-warning/80",
    gradientTo: "to-warning/30",
  },
  destructive: {
    bg: "bg-destructive/10",
    text: "text-destructive",
    icon: "text-destructive",
    stroke: "var(--color-destructive, hsl(0, 84%, 60%))",
    fill: "var(--color-destructive, hsl(0, 84%, 60%))",
    gradientFrom: "from-destructive/80",
    gradientTo: "to-destructive/30",
  },
};

function useCountUp(target: number, duration = 800): string {
  const [display, setDisplay] = useState("0");
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const start = performance.now();

    const tick = (now: number) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = Math.round(eased * target);
      setDisplay(current.toLocaleString());

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration]);

  return display;
}

interface MiniSparklineProps {
  data: number[];
  stroke: string;
  fill: string;
}

const MiniSparkline = ({ data, stroke, fill }: MiniSparklineProps) => {
  const chartData = data.map((v, i) => ({ index: i, value: v }));

  return (
    <AreaChart
      width={80}
      height={32}
      data={chartData}
      margin={{ top: 2, right: 0, bottom: 2, left: 0 }}
    >
      <defs>
        <linearGradient id={`sparkGrad-${stroke}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={fill} stopOpacity={0.3} />
          <stop offset="100%" stopColor={fill} stopOpacity={0.02} />
        </linearGradient>
      </defs>
      <Area
        type="monotone"
        dataKey="value"
        stroke={stroke}
        strokeWidth={1.5}
        fill={`url(#sparkGrad-${stroke})`}
        dot={false}
        isAnimationActive={false}
      />
    </AreaChart>
  );
};

const KPICards = () => {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {kpis.map((kpi, i) => {
        const colors = colorMap[kpi.color];
        const Icon = kpi.icon;
        const TrendIcon = kpi.trend === "up" ? TrendingUp : TrendingDown;
        const animatedValue = useCountUp(kpi.value);
        const sparklineData = sparklineDataMap[kpi.label] ?? [];

        return (
          <div
            key={kpi.label}
            className="dashboard-card cursor-default overflow-hidden rounded-xl p-0 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg animate-fade-in"
            style={{ animationDelay: `${i * 80}ms` }}
          >
            <div
              className={`h-0.5 bg-gradient-to-r ${colors.gradientFrom} ${colors.gradientTo}`}
            />

            <div className="p-5">
              <div className="flex items-start justify-between">
                <div>
                  <p className="kpi-label">{kpi.label}</p>
                  <p className="kpi-value mt-1">{animatedValue}</p>
                </div>

                <div className="relative flex h-10 w-20 items-center justify-center">
                  <div className="absolute inset-0 flex items-center justify-center opacity-60">
                    <MiniSparkline
                      data={sparklineData}
                      stroke={colors.stroke}
                      fill={colors.fill}
                    />
                  </div>
                  <div
                    className={`relative z-10 flex h-10 w-10 items-center justify-center rounded-xl ${colors.bg}`}
                  >
                    <Icon className={`h-5 w-5 ${colors.icon}`} />
                  </div>
                </div>
              </div>

              <div className="mt-3 flex items-center gap-1.5 text-xs">
                <TrendIcon
                  className={`h-3.5 w-3.5 ${kpi.trend === "up" ? "text-success" : "text-destructive"}`}
                />
                <span className={kpi.trend === "up" ? "text-success" : "text-destructive"}>
                  {kpi.change}
                </span>
                <span className="text-muted-foreground">较上月</span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default KPICards;
