// Profiling + heuristic risk scan over local DuckDB tables.
import { duckdbManager, TableInfo } from "./client";

export interface ColumnProfile {
  name: string;
  type: string;
  nulls: number;
  nullPct: number;
  distinct: number;
  min?: string | number | null;
  max?: string | number | null;
  topValues?: { value: string; count: number }[];
}

export interface TableProfile {
  table: string;
  rows: number;
  columns: ColumnProfile[];
}

const NUMERIC = /INT|DECIMAL|DOUBLE|REAL|FLOAT|NUMERIC|BIGINT|HUGEINT|SMALLINT/i;
const TEMPORAL = /DATE|TIME|TIMESTAMP/i;

export async function profileTable(table: string): Promise<TableProfile> {
  const info = duckdbManager.listTables().find((t) => t.name === table);
  if (!info) throw new Error(`未找到表 ${table}`);
  const cols: ColumnProfile[] = [];
  for (const c of info.columns) {
    const numeric = NUMERIC.test(c.type);
    const temporal = TEMPORAL.test(c.type);
    const sqlMinMax =
      numeric || temporal
        ? `, MIN("${c.name}")::VARCHAR AS mn, MAX("${c.name}")::VARCHAR AS mx`
        : "";
    const q = `SELECT
        COUNT(*) - COUNT("${c.name}") AS nulls,
        COUNT(DISTINCT "${c.name}") AS distincts
        ${sqlMinMax}
      FROM "${table}";`;
    const r = await duckdbManager.runQuery(q, 1);
    const row = r.rows[0] as any;
    const nulls = Number(row.nulls || 0);
    const distinct = Number(row.distincts || 0);
    let topValues: ColumnProfile["topValues"];
    if (!numeric && !temporal && distinct > 0 && distinct < 50) {
      const tv = await duckdbManager.runQuery(
        `SELECT "${c.name}"::VARCHAR AS value, COUNT(*)::BIGINT AS c FROM "${table}" WHERE "${c.name}" IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 5;`,
        10,
      );
      topValues = tv.rows.map((r: any) => ({ value: r.value, count: Number(r.c) }));
    }
    cols.push({
      name: c.name,
      type: c.type,
      nulls,
      nullPct: info.rows ? +(nulls / info.rows * 100).toFixed(1) : 0,
      distinct,
      min: row.mn ?? null,
      max: row.mx ?? null,
      topValues,
    });
  }
  return { table, rows: info.rows, columns: cols };
}

export function summarizeSchemaForPrompt(): string {
  const t = duckdbManager.listTables();
  if (!t.length) return "";
  const lines: string[] = [
    "# 已连接的本地数据 (DuckDB)",
    "你可以通过 <tool name=\"query_sql\" id=\"...\">SELECT ...</tool> 直接查询以下表（DuckDB 方言、只读）。",
    "",
  ];
  for (const tbl of t) {
    lines.push(
      `· **${tbl.name}** (${tbl.rows.toLocaleString()} 行，来源 ${tbl.source})`,
    );
    const cols = tbl.columns
      .slice(0, 24)
      .map((c) => `${c.name}:${c.type}`)
      .join(", ");
    lines.push(`  列: ${cols}${tbl.columns.length > 24 ? " …" : ""}`);
  }
  return lines.join("\n");
}

export interface RiskFinding {
  severity: "high" | "medium" | "low";
  table: string;
  column?: string;
  kind: string;
  detail: string;
  evidence?: Record<string, unknown>;
}

/** Heuristic risk scan — runs only on tables that look like QA / test data. */
export async function riskScan(table?: string): Promise<RiskFinding[]> {
  const targets = table
    ? duckdbManager.listTables().filter((t) => t.name === table)
    : duckdbManager.listTables();
  const findings: RiskFinding[] = [];

  for (const t of targets) {
    const cols = t.columns.map((c) => c.name.toLowerCase());

    // 1. High null ratio on critical columns
    for (const c of t.columns) {
      const r = await duckdbManager.runQuery(
        `SELECT (COUNT(*) - COUNT("${c.name}"))::DOUBLE / NULLIF(COUNT(*),0) AS np FROM "${t.name}";`,
        1,
      );
      const np = Number((r.rows[0] as any).np || 0);
      if (np > 0.3) {
        findings.push({
          severity: np > 0.6 ? "high" : "medium",
          table: t.name,
          column: c.name,
          kind: "null-ratio",
          detail: `字段 ${c.name} 缺失率 ${(np * 100).toFixed(1)}%`,
          evidence: { nullPct: np },
        });
      }
    }

    // 2. Status / result column imbalance (Fail / Block / Open / P0)
    const statusCol = ["status", "result", "severity", "priority"].find((s) =>
      cols.includes(s),
    );
    if (statusCol) {
      const r = await duckdbManager.runQuery(
        `SELECT "${statusCol}"::VARCHAR AS v, COUNT(*)::DOUBLE / SUM(COUNT(*)) OVER () AS pct, COUNT(*)::BIGINT AS c FROM "${t.name}" GROUP BY 1 ORDER BY pct DESC;`,
        50,
      );
      for (const row of r.rows as any[]) {
        const v = String(row.v ?? "").toLowerCase();
        const pct = Number(row.pct);
        if (/fail|block|open|p0|critical|高/.test(v) && pct > 0.15) {
          findings.push({
            severity: pct > 0.3 ? "high" : "medium",
            table: t.name,
            column: statusCol,
            kind: "status-imbalance",
            detail: `${statusCol}=${row.v} 占 ${(pct * 100).toFixed(1)}% (${row.c} 条)`,
            evidence: { value: row.v, pct, count: Number(row.c) },
          });
        }
      }
    }

    // 3. Long-runner: age_days > P95 数量
    const ageCol = ["age_days", "age", "days_open"].find((c) => cols.includes(c));
    if (ageCol) {
      const r = await duckdbManager.runQuery(
        `SELECT quantile_cont("${ageCol}", 0.95) AS p95,
                COUNT(*) FILTER (WHERE "${ageCol}" > 60)::BIGINT AS long_n,
                COUNT(*)::BIGINT AS total
         FROM "${t.name}";`,
        1,
      );
      const row = r.rows[0] as any;
      const longN = Number(row.long_n || 0);
      const total = Number(row.total || 1);
      if (longN / total > 0.1) {
        findings.push({
          severity: longN / total > 0.25 ? "high" : "medium",
          table: t.name,
          column: ageCol,
          kind: "long-runner",
          detail: `${longN} 条记录 ${ageCol}>60 天 (${((longN / total) * 100).toFixed(1)}%)`,
          evidence: { p95: row.p95, long: longN, total },
        });
      }
    }

    // 4. Time series z-score anomaly on monthly count
    const dateCol = t.columns.find((c) => TEMPORAL.test(c.type));
    if (dateCol) {
      try {
        const r = await duckdbManager.runQuery(
          `WITH m AS (
             SELECT date_trunc('month', "${dateCol.name}") AS ym, COUNT(*)::DOUBLE AS c
             FROM "${t.name}" WHERE "${dateCol.name}" IS NOT NULL GROUP BY 1
           ), s AS (SELECT AVG(c) avg_c, STDDEV(c) sd FROM m)
           SELECT m.ym::VARCHAR AS ym, m.c, (m.c - s.avg_c)/NULLIF(s.sd,0) AS z
           FROM m, s ORDER BY ABS((m.c - s.avg_c)/NULLIF(s.sd,0)) DESC LIMIT 3;`,
          5,
        );
        for (const row of r.rows as any[]) {
          const z = Number(row.z);
          if (Math.abs(z) > 2) {
            findings.push({
              severity: Math.abs(z) > 3 ? "high" : "medium",
              table: t.name,
              column: dateCol.name,
              kind: "time-anomaly",
              detail: `${row.ym} 行数 ${Number(row.c)} (z=${z.toFixed(2)})`,
              evidence: { month: row.ym, count: Number(row.c), z },
            });
          }
        }
      } catch {}
    }
  }
  return findings.slice(0, 20);
}
