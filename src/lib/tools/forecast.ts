// Holt-Winters double exponential smoothing (level + trend), no seasonality.
// Pure TS — no external deps. Returns point forecast and approximate 95% CI from in-sample residuals.

export interface ForecastPoint {
  step: number;
  label?: string;
  value: number;
  lower: number;
  upper: number;
}

export interface ForecastResult {
  history: { label?: string; value: number }[];
  forecast: ForecastPoint[];
  alpha: number;
  beta: number;
  rmse: number;
}

// Grid-search alpha/beta minimizing in-sample SSE.
function fitHoltWinters(y: number[]) {
  let best = { alpha: 0.5, beta: 0.1, sse: Infinity };
  const grid = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9];
  for (const a of grid) {
    for (const b of grid) {
      const sse = sseFor(y, a, b);
      if (sse < best.sse) best = { alpha: a, beta: b, sse };
    }
  }
  return best;
}

function sseFor(y: number[], alpha: number, beta: number) {
  if (y.length < 2) return Infinity;
  let level = y[0];
  let trend = y[1] - y[0];
  let sse = 0;
  for (let t = 1; t < y.length; t++) {
    const yhat = level + trend;
    const err = y[t] - yhat;
    sse += err * err;
    const newLevel = alpha * y[t] + (1 - alpha) * (level + trend);
    const newTrend = beta * (newLevel - level) + (1 - beta) * trend;
    level = newLevel;
    trend = newTrend;
  }
  return sse;
}

export function forecastSeries(
  series: number[],
  horizon: number,
  labels?: string[],
): ForecastResult {
  if (series.length < 3) {
    throw new Error("forecast: series too short (need ≥3 points)");
  }
  if (horizon < 1 || horizon > 60) {
    throw new Error("forecast: horizon must be 1..60");
  }
  const y = series.map(Number);
  const { alpha, beta } = fitHoltWinters(y);

  // Replay to get final level/trend + residuals
  let level = y[0];
  let trend = y[1] - y[0];
  const residuals: number[] = [];
  for (let t = 1; t < y.length; t++) {
    const yhat = level + trend;
    residuals.push(y[t] - yhat);
    const newLevel = alpha * y[t] + (1 - alpha) * (level + trend);
    const newTrend = beta * (newLevel - level) + (1 - beta) * trend;
    level = newLevel;
    trend = newTrend;
  }
  const rmse = Math.sqrt(residuals.reduce((s, e) => s + e * e, 0) / Math.max(residuals.length, 1));
  // 95% CI widens with horizon (random-walk style: sigma*sqrt(h))
  const z = 1.96;

  const forecast: ForecastPoint[] = [];
  const lastLabel = labels?.[labels.length - 1];
  for (let h = 1; h <= horizon; h++) {
    const point = level + h * trend;
    const band = z * rmse * Math.sqrt(h);
    forecast.push({
      step: h,
      label: nextLabel(lastLabel, h),
      value: round(point),
      lower: round(point - band),
      upper: round(point + band),
    });
  }

  return {
    history: y.map((v, i) => ({ label: labels?.[i], value: round(v) })),
    forecast,
    alpha,
    beta,
    rmse: round(rmse),
  };
}

function round(n: number) {
  return Math.round(n * 1000) / 1000;
}

// Best-effort label extrapolation for YYYY-MM, YYYY-MM-DD, or simple ints.
function nextLabel(last: string | undefined, offset: number): string | undefined {
  if (!last) return undefined;
  const ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(last);
  if (ymd) {
    const d = new Date(`${last}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + offset);
    return d.toISOString().slice(0, 10);
  }
  const ym = /^(\d{4})-(\d{2})$/.exec(last);
  if (ym) {
    const y = +ym[1], m = +ym[2] - 1 + offset;
    const ny = y + Math.floor(m / 12);
    const nm = ((m % 12) + 12) % 12;
    return `${ny}-${String(nm + 1).padStart(2, "0")}`;
  }
  const n = Number(last);
  if (!Number.isNaN(n)) return String(n + offset);
  return `${last}+${offset}`;
}
