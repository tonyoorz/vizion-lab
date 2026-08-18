// Agent observability: OpenTelemetry-style trace/span model persisted locally.
// Every agent run opens a trace; each LLM call / SQL query / tool invocation is a
// span with full input & output snapshots so a run can be replayed and debugged.

import { idbDelete, idbGetAll, idbGetByIndex, idbPut, idbClear, idbDeleteByIndex } from "@/lib/memory/db";

export type SpanKind = "agent" | "llm" | "sql" | "tool" | "retrieval" | "local";
export type SpanStatus = "running" | "ok" | "error";

export interface SpanRecord {
  id: string;
  traceId: string;
  parentId?: string;
  name: string;
  kind: SpanKind;
  status: SpanStatus;
  startTs: number;
  endTs?: number;
  durationMs?: number;
  input?: string;
  output?: string;
  error?: string;
  attrs?: Record<string, string | number | boolean | null | undefined>;
}

export interface TraceRecord {
  id: string;
  type: string; // ask-data | test-agent | chat …
  title: string;
  status: SpanStatus;
  startTs: number;
  endTs?: number;
  durationMs?: number;
  error?: string;
  spanCount: number;
  errorCount: number;
  meta?: Record<string, unknown>;
}

const MAX_SNAPSHOT = 8000;
const clip = (v: unknown): string | undefined => {
  if (v === undefined || v === null) return undefined;
  const s = typeof v === "string" ? v : safeStringify(v);
  return s.length > MAX_SNAPSHOT ? `${s.slice(0, MAX_SNAPSHOT)}\n…(已截断 ${s.length - MAX_SNAPSHOT} 字符)` : s;
};

function safeStringify(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2) ?? String(v);
  } catch {
    return String(v);
  }
}

const uid = () =>
  (globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);

export class Tracer {
  readonly traceId: string;
  private trace: TraceRecord;
  private stack: string[] = [];
  private spans = new Map<string, SpanRecord>();

  constructor(type: string, title: string, meta?: Record<string, unknown>) {
    this.traceId = uid();
    this.trace = {
      id: this.traceId,
      type,
      title,
      status: "running",
      startTs: Date.now(),
      spanCount: 0,
      errorCount: 0,
      meta,
    };
    void idbPut("traces", this.trace);
  }

  /** Open a span; returns its id. Nests under the currently open span. */
  start(name: string, kind: SpanKind, input?: unknown, attrs?: SpanRecord["attrs"]): string {
    const span: SpanRecord = {
      id: uid(),
      traceId: this.traceId,
      parentId: this.stack[this.stack.length - 1],
      name,
      kind,
      status: "running",
      startTs: Date.now(),
      input: clip(input),
      attrs,
    };
    this.spans.set(span.id, span);
    this.stack.push(span.id);
    this.trace.spanCount += 1;
    void idbPut("spans", span);
    void idbPut("traces", this.trace);
    return span.id;
  }

  end(spanId: string, res: { output?: unknown; error?: unknown; attrs?: SpanRecord["attrs"] } = {}) {
    const span = this.spans.get(spanId);
    if (!span) return;
    span.endTs = Date.now();
    span.durationMs = span.endTs - span.startTs;
    span.output = clip(res.output);
    if (res.error) {
      span.status = "error";
      span.error = String((res.error as Error)?.message ?? res.error);
      this.trace.errorCount += 1;
    } else {
      span.status = "ok";
    }
    if (res.attrs) span.attrs = { ...span.attrs, ...res.attrs };
    const i = this.stack.lastIndexOf(spanId);
    if (i >= 0) this.stack.splice(i, 1);
    void idbPut("spans", span);
    void idbPut("traces", this.trace);
  }

  /** Convenience wrapper: traces an async unit of work. */
  async span<T>(
    name: string,
    kind: SpanKind,
    input: unknown,
    fn: () => Promise<T>,
    mapOutput?: (v: T) => unknown,
  ): Promise<T> {
    const id = this.start(name, kind, input);
    try {
      const out = await fn();
      this.end(id, { output: mapOutput ? mapOutput(out) : out });
      return out;
    } catch (e) {
      this.end(id, { error: e });
      throw e;
    }
  }

  finish(err?: unknown) {
    this.trace.endTs = Date.now();
    this.trace.durationMs = this.trace.endTs - this.trace.startTs;
    if (err) {
      this.trace.status = "error";
      this.trace.error = String((err as Error)?.message ?? err);
    } else {
      this.trace.status = this.trace.errorCount > 0 ? "error" : "ok";
    }
    void idbPut("traces", this.trace);
    return this.trace;
  }
}

export const startTrace = (type: string, title: string, meta?: Record<string, unknown>) =>
  new Tracer(type, title, meta);

export const listTraces = async (): Promise<TraceRecord[]> =>
  (await idbGetAll<TraceRecord>("traces")).sort((a, b) => b.startTs - a.startTs);

export const getSpans = async (traceId: string): Promise<SpanRecord[]> =>
  (await idbGetByIndex<SpanRecord>("spans", "traceId", traceId)).sort((a, b) => a.startTs - b.startTs);

export const deleteTrace = async (traceId: string) => {
  await idbDeleteByIndex("spans", "traceId", traceId);
  await idbDelete("traces", traceId);
};

export const clearTraces = async () => {
  await idbClear("spans");
  await idbClear("traces");
};

/** Build a parent→children tree for the call-tree view. */
export interface SpanNode extends SpanRecord {
  children: SpanNode[];
  depth: number;
}

export function buildSpanTree(spans: SpanRecord[]): SpanNode[] {
  const map = new Map<string, SpanNode>();
  spans.forEach((s) => map.set(s.id, { ...s, children: [], depth: 0 }));
  const roots: SpanNode[] = [];
  map.forEach((node) => {
    const parent = node.parentId ? map.get(node.parentId) : undefined;
    if (parent) {
      node.depth = parent.depth + 1;
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  });
  const fixDepth = (n: SpanNode, d: number) => {
    n.depth = d;
    n.children.forEach((c) => fixDepth(c, d + 1));
  };
  roots.forEach((r) => fixDepth(r, 0));
  return roots;
}

export function flattenTree(nodes: SpanNode[]): SpanNode[] {
  const out: SpanNode[] = [];
  const walk = (n: SpanNode) => {
    out.push(n);
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return out;
}
