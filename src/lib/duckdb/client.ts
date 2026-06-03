// Browser-side DuckDB client. Singleton + safe query wrapper.
// All data stays local — files are registered into the WASM file system.

import * as duckdb from "@duckdb/duckdb-wasm";
import { isSafeReadOnlySql } from "./safety";

export interface TableInfo {
  name: string;
  source: string; // file name
  rows: number;
  columns: { name: string; type: string }[];
}

export interface RunResult {
  columns: string[];
  rows: Record<string, unknown>[];
  rowCount: number;
  truncated: boolean;
  ms: number;
}

class DuckDBManager {
  private dbPromise: Promise<duckdb.AsyncDuckDB> | null = null;
  private worker: Worker | null = null;
  private workerUrl: string | null = null;
  tables: TableInfo[] = [];
  private listeners = new Set<() => void>();

  subscribe(fn: () => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private emit() {
    this.listeners.forEach((f) => f());
  }

  private async init(): Promise<duckdb.AsyncDuckDB> {
    if (this.dbPromise) return this.dbPromise;
    this.dbPromise = (async () => {
      const bundles = duckdb.getJsDelivrBundles();
      const bundle = await duckdb.selectBundle(bundles);
      const workerSrc = `importScripts("${bundle.mainWorker!}");`;
      this.workerUrl = URL.createObjectURL(
        new Blob([workerSrc], { type: "text/javascript" }),
      );
      this.worker = new Worker(this.workerUrl);
      const logger = new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING);
      const db = new duckdb.AsyncDuckDB(logger, this.worker);
      await db.instantiate(bundle.mainModule, bundle.pthreadWorker ?? undefined);
      // lock down: no remote http, no external access (best-effort)
      const conn = await db.connect();
      try {
        await conn.query("SET enable_external_access=false;");
      } catch {}
      await conn.close();
      return db;
    })();
    return this.dbPromise;
  }

  async ready() {
    return this.init();
  }

  private sanitizeTableName(raw: string): string {
    const base = raw.replace(/\.[^.]+$/, "").replace(/[^a-zA-Z0-9_]/g, "_");
    let name = /^[a-zA-Z_]/.test(base) ? base : `t_${base}`;
    let i = 1;
    while (this.tables.some((t) => t.name === name)) {
      name = `${base || "t"}_${i++}`;
    }
    return name;
  }

  /** Register CSV / Parquet / JSON file as a table. */
  async registerTabular(file: File): Promise<TableInfo> {
    const db = await this.init();
    const buf = new Uint8Array(await file.arrayBuffer());
    const lower = file.name.toLowerCase();
    const virtual = `${Date.now()}_${file.name.replace(/[^a-zA-Z0-9_.\-]/g, "_")}`;
    await db.registerFileBuffer(virtual, buf);
    const table = this.sanitizeTableName(file.name);

    let reader: string;
    if (lower.endsWith(".parquet")) reader = `read_parquet('${virtual}')`;
    else if (lower.endsWith(".json") || lower.endsWith(".ndjson"))
      reader = `read_json_auto('${virtual}')`;
    else reader = `read_csv_auto('${virtual}', SAMPLE_SIZE=-1)`;

    const conn = await db.connect();
    try {
      await conn.query(`CREATE OR REPLACE TABLE "${table}" AS SELECT * FROM ${reader};`);
      const info = await this.describeTable(table, file.name);
      this.tables.push(info);
      this.emit();
      return info;
    } finally {
      await conn.close();
    }
  }

  /** Attach a .duckdb file (read-only) and register each of its tables. */
  async registerDuckdbFile(file: File): Promise<TableInfo[]> {
    const db = await this.init();
    const buf = new Uint8Array(await file.arrayBuffer());
    const virtual = `${Date.now()}_${file.name.replace(/[^a-zA-Z0-9_.\-]/g, "_")}`;
    await db.registerFileBuffer(virtual, buf);
    const alias = this.sanitizeTableName(file.name.replace(/\.duckdb$/i, "")) + "_db";
    const conn = await db.connect();
    const added: TableInfo[] = [];
    try {
      await conn.query(`ATTACH '${virtual}' AS "${alias}" (READ_ONLY);`);
      // List tables in attached db
      const tbls = await conn.query(
        `SELECT table_schema, table_name FROM information_schema.tables WHERE table_catalog = '${alias}' AND table_type IN ('BASE TABLE','VIEW');`,
      );
      for (const row of tbls.toArray() as any[]) {
        const schema = row.table_schema || "main";
        const tname = row.table_name;
        const local = this.sanitizeTableName(tname);
        // Create a view in the default db that points to the attached table — easier for the model.
        await conn.query(
          `CREATE OR REPLACE VIEW "${local}" AS SELECT * FROM "${alias}"."${schema}"."${tname}";`,
        );
        const info = await this.describeTable(local, `${file.name}::${tname}`);
        this.tables.push(info);
        added.push(info);
      }
    } finally {
      await conn.close();
    }
    this.emit();
    return added;
  }

  private async describeTable(table: string, source: string): Promise<TableInfo> {
    const db = await this.init();
    const conn = await db.connect();
    try {
      const cols = await conn.query(
        `SELECT column_name, data_type FROM information_schema.columns WHERE table_name='${table}' ORDER BY ordinal_position;`,
      );
      const cnt = await conn.query(`SELECT COUNT(*)::BIGINT AS n FROM "${table}";`);
      const n = Number((cnt.toArray()[0] as any).n);
      const columns = (cols.toArray() as any[]).map((r) => ({
        name: String(r.column_name),
        type: String(r.data_type),
      }));
      return { name: table, source, rows: n, columns };
    } finally {
      await conn.close();
    }
  }

  listTables(): TableInfo[] {
    return this.tables.slice();
  }

  /** Internal helper: dump a registered table as CSV text (bypasses safety; for Pyodide). */
  async exportTableCsv(table: string, limit = 50000): Promise<string> {
    const db = await this.init();
    const conn = await db.connect();
    try {
      // Validate table name (alnum + underscore only)
      if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(table)) {
        throw new Error(`非法表名: ${table}`);
      }
      const res = await conn.query(
        `SELECT * FROM "${table}" LIMIT ${Math.max(1, Math.floor(limit))}`,
      );
      const cols = res.schema.fields.map((f: any) => f.name as string);
      const rows = res.toArray() as any[];
      const esc = (v: unknown) => {
        if (v == null) return "";
        let s: string;
        if (typeof v === "bigint") s = v.toString();
        else if (v instanceof Date) s = v.toISOString();
        else if (typeof v === "object") s = JSON.stringify(v);
        else s = String(v);
        if (/[",\n\r]/.test(s)) s = `"${s.replace(/"/g, '""')}"`;
        return s;
      };
      const lines = [cols.join(",")];
      for (const r of rows) lines.push(cols.map((c) => esc((r as any)[c])).join(","));
      return lines.join("\n");
    } finally {
      await conn.close();
    }
  }

  reset() {
    this.tables = [];
    this.emit();
  }


  /** Run a read-only SQL statement. Throws if the statement is unsafe. */
  async runQuery(sql: string, maxRows = 500): Promise<RunResult> {
    const trimmed = sql.trim().replace(/;+\s*$/, "");
    if (!isSafeReadOnlySql(trimmed)) {
      throw new Error("仅允许只读查询 (SELECT / WITH / DESCRIBE / SHOW / PRAGMA)。");
    }
    const db = await this.init();
    const conn = await db.connect();
    const start = performance.now();
    try {
      const result = await conn.query(trimmed);
      const all = result.toArray() as any[];
      const rowCount = all.length;
      const sliced = all.slice(0, maxRows);
      const cols = result.schema.fields.map((f: any) => f.name as string);
      // Convert BigInts to numbers for JSON serialization
      const rows = sliced.map((r) => {
        const o: Record<string, unknown> = {};
        for (const c of cols) {
          const v = (r as any)[c];
          o[c] =
            typeof v === "bigint"
              ? Number(v)
              : v instanceof Date
                ? v.toISOString()
                : v;
        }
        return o;
      });
      return {
        columns: cols,
        rows,
        rowCount,
        truncated: rowCount > maxRows,
        ms: Math.round(performance.now() - start),
      };
    } finally {
      await conn.close();
    }
  }
}

export const duckdbManager = new DuckDBManager();

export function isDuckdbFile(file: File): boolean {
  const n = file.name.toLowerCase();
  return (
    n.endsWith(".duckdb") ||
    n.endsWith(".csv") ||
    n.endsWith(".tsv") ||
    n.endsWith(".parquet") ||
    n.endsWith(".ndjson") ||
    (n.endsWith(".json") && file.size < 50 * 1024 * 1024)
  );
}
