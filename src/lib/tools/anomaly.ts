// Anomaly detection: z-score (>3σ) ∪ IQR (1.5× outside Q1/Q3).

export interface AnomalyPoint {
  index: number;
  label?: string;
  value: number;
  z: number;
  methods: ("zscore" | "iqr")[];
}

export interface AnomalyResult {
  total: number;
  outliers: AnomalyPoint[];
  stats: { mean: number; std: number; q1: number; median: number; q3: number; iqr: number };
}

export function detectAnomalies(series: number[], labels?: string[]): AnomalyResult {
  const n = series.length;
  if (n < 4) throw new Error("detect_anomaly: need ≥4 points");
  const y = series.map(Number);
  const mean = y.reduce((s, v) => s + v, 0) / n;
  const variance = y.reduce((s, v) => s + (v - mean) ** 2, 0) / n;
  const std = Math.sqrt(variance);

  const sorted = [...y].sort((a, b) => a - b);
  const q = (p: number) => {
    const i = p * (sorted.length - 1);
    const lo = Math.floor(i), hi = Math.ceil(i);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
  };
  const q1 = q(0.25), median = q(0.5), q3 = q(0.75);
  const iqr = q3 - q1;
  const lowF = q1 - 1.5 * iqr, hiF = q3 + 1.5 * iqr;

  const outliers: AnomalyPoint[] = [];
  y.forEach((v, i) => {
    const z = std > 0 ? (v - mean) / std : 0;
    const methods: ("zscore" | "iqr")[] = [];
    if (Math.abs(z) > 3) methods.push("zscore");
    if (v < lowF || v > hiF) methods.push("iqr");
    if (methods.length) {
      outliers.push({
        index: i,
        label: labels?.[i],
        value: round(v),
        z: round(z),
        methods,
      });
    }
  });

  return {
    total: n,
    outliers,
    stats: {
      mean: round(mean),
      std: round(std),
      q1: round(q1),
      median: round(median),
      q3: round(q3),
      iqr: round(iqr),
    },
  };
}

function round(n: number) {
  return Math.round(n * 1000) / 1000;
}
