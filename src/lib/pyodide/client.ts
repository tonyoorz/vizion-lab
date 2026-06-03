// Browser-side Python sandbox (Pyodide) for the data-analysis agent.
// Runs in a dedicated Worker. Loads pyodide from jsDelivr on first use.
// Tables registered in DuckDB are exported to CSV and injected as pandas DataFrames.

import { duckdbManager } from "@/lib/duckdb/client";

export interface PyRunResult {
  ok: boolean;
  stdout: string;
  figures: string[]; // base64 PNG
  value?: string; // repr of last expression, if any
  error?: string;
  ms: number;
  tablesLoaded: string[];
}

const PYODIDE_VERSION = "0.26.4";
const PYODIDE_INDEX = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

const WORKER_SRC = `
self.importScripts("${PYODIDE_INDEX}pyodide.js");
let pyReady = null;
async function init() {
  if (pyReady) return pyReady;
  pyReady = (async () => {
    const py = await loadPyodide({ indexURL: "${PYODIDE_INDEX}" });
    await py.loadPackage(["numpy", "pandas", "matplotlib", "scipy", "scikit-learn"]);
    py.runPython(\`
import matplotlib
matplotlib.use("AGG")
import matplotlib.pyplot as plt
import io, base64, sys
_FIGS = []
_orig_show = plt.show
def _capture_show(*a, **k):
    for n in plt.get_fignums():
        fig = plt.figure(n)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", dpi=110)
        _FIGS.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    plt.close("all")
plt.show = _capture_show
\`);
    return py;
  })();
  return pyReady;
}

self.onmessage = async (e) => {
  const { id, type, code, dataframes } = e.data;
  if (type === "ping") {
    try { await init(); self.postMessage({ id, ok: true, ready: true }); }
    catch (err) { self.postMessage({ id, ok: false, error: String(err) }); }
    return;
  }
  if (type !== "run") return;
  const t0 = performance.now();
  try {
    const py = await init();
    // reset captured figures
    py.runPython("_FIGS.clear()");
    // Inject dataframes
    const names = Object.keys(dataframes || {});
    for (const name of names) {
      py.globals.set("_csv_text_" + name, dataframes[name]);
      py.runPython(
        "import pandas as pd, io\\n" +
        name + " = pd.read_csv(io.StringIO(_csv_text_" + name + "))"
      );
    }
    // Redirect stdout/stderr
    py.runPython("import sys, io as _io\\nsys.stdout = _io.StringIO()\\nsys.stderr = sys.stdout");
    let valueRepr = null;
    try {
      const result = await py.runPythonAsync(code);
      if (result !== undefined && result !== null) {
        try {
          py.globals.set("_lv", result);
          valueRepr = py.runPython("repr(_lv)[:4000]");
        } catch (_) {}
        try { if (result && typeof result.destroy === "function") result.destroy(); } catch (_) {}
      }
    } catch (err) {
      const stdout = py.runPython("sys.stdout.getvalue()");
      const figs = py.runPython("list(_FIGS)").toJs();
      self.postMessage({
        id, ok: false, error: String(err && err.message || err),
        stdout, figures: figs, ms: Math.round(performance.now() - t0),
      });
      return;
    }
    const stdout = py.runPython("sys.stdout.getvalue()");
    const figs = py.runPython("list(_FIGS)").toJs();
    self.postMessage({
      id, ok: true, stdout, figures: figs, value: valueRepr,
      ms: Math.round(performance.now() - t0),
    });
  } catch (err) {
    self.postMessage({
      id, ok: false, error: String(err && err.message || err),
      stdout: "", figures: [], ms: Math.round(performance.now() - t0),
    });
  }
};
`;

class PyodideManager {
  private worker: Worker | null = null;
  private workerUrl: string | null = null;
  private nextId = 1;
  private pending = new Map<number, (msg: any) => void>();
  private initPromise: Promise<void> | null = null;
  private listeners = new Set<() => void>();
  status: "idle" | "loading" | "ready" | "error" = "idle";

  subscribe(fn: () => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private emit() {
    this.listeners.forEach((f) => f());
  }
  private setStatus(s: typeof this.status) {
    this.status = s;
    this.emit();
  }

  private ensureWorker() {
    if (this.worker) return;
    this.workerUrl = URL.createObjectURL(
      new Blob([WORKER_SRC], { type: "text/javascript" }),
    );
    this.worker = new Worker(this.workerUrl);
    this.worker.onmessage = (e) => {
      const { id } = e.data || {};
      const cb = this.pending.get(id);
      if (cb) {
        this.pending.delete(id);
        cb(e.data);
      }
    };
    this.worker.onerror = (e) => {
      this.setStatus("error");
      console.error("[pyodide worker error]", e.message);
    };
  }

  private call(payload: any, timeoutMs: number): Promise<any> {
    this.ensureWorker();
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        // Hard reset on timeout
        try { this.worker?.terminate(); } catch {}
        this.worker = null;
        this.setStatus("idle");
        reject(new Error(`Python 执行超时 (${Math.round(timeoutMs / 1000)}s)`));
      }, timeoutMs);
      this.pending.set(id, (msg) => {
        clearTimeout(timer);
        resolve(msg);
      });
      this.worker!.postMessage({ id, ...payload });
    });
  }

  async warmup(): Promise<void> {
    if (this.initPromise) return this.initPromise;
    this.setStatus("loading");
    this.initPromise = (async () => {
      try {
        await this.call({ type: "ping" }, 120_000);
        this.setStatus("ready");
      } catch (e) {
        this.setStatus("error");
        throw e;
      }
    })();
    return this.initPromise;
  }

  /** Run Python code; auto-loads the named tables (or all if names omitted) as DataFrames. */
  async run(code: string, tables?: string[], timeoutMs = 30_000): Promise<PyRunResult> {
    await this.warmup();
    const targetTables =
      tables && tables.length
        ? tables
        : duckdbManager.listTables().map((t) => t.name);
    const dataframes: Record<string, string> = {};
    for (const name of targetTables) {
      try {
        dataframes[name] = await duckdbManager.exportTableCsv(name, 50_000);
      } catch (e) {
        // skip — table may not exist
      }
    }
    const msg = await this.call({ type: "run", code, dataframes }, timeoutMs);
    return {
      ok: !!msg.ok,
      stdout: msg.stdout || "",
      figures: Array.isArray(msg.figures) ? msg.figures : [],
      value: msg.value || undefined,
      error: msg.error,
      ms: msg.ms || 0,
      tablesLoaded: Object.keys(dataframes),
    };
  }
}

export const pyodideManager = new PyodideManager();
