// Parses the streaming assistant text into structured segments.
// Tags supported:
//   <think>...</think>           reasoning
//   <plan>...</plan>             ordered task checklist (one item per line)
//   <step title="x" source="y">summary</step>   tool/analysis steps
//   <chart type="...">{ JSON }</chart>          inline chart spec
//   <tool name="..." id="...">payload</tool>    agent tool invocation (executed locally)
//   <cite source="x">label</cite> inline citations (kept inside text segments)
//   <followup>question</followup>  suggested next questions (one per line)
// Anything else is treated as final markdown body text.

export interface TestCaseItem {
  id: string;
  title: string;
  priority?: string;
  type?: string;
  preconditions?: string[];
  steps?: string[];
  expected?: string[];
  data?: string;
  linked_defect?: string;
  linked_req?: string;
  rationale?: string;
  tags?: string[];
}

export type AgentSegment =
  | { kind: "think"; text: string; closed: boolean }
  | { kind: "plan"; items: string[]; closed: boolean }
  | {
      kind: "step";
      title: string;
      source?: string;
      text: string;
      closed: boolean;
    }
  | {
      kind: "chart";
      chartType: "line" | "bar" | "area" | "pie";
      title?: string;
      text: string;
      closed: boolean;
    }
  | {
      kind: "tool";
      toolName: string;
      toolId: string;
      args?: Record<string, string>;
      text: string;
      closed: boolean;
    }
  | {
      kind: "testcases";
      module?: string;
      title?: string;
      items: TestCaseItem[];
      raw: string;
      closed: boolean;
    }
  | { kind: "followup"; items: string[]; closed: boolean }
  | { kind: "text"; text: string };

const TAG_RE =
  /<(think|plan|step|chart|tool|testcases|followup)(\s[^>]*)?>|<\/(think|plan|step|chart|tool|testcases|followup)>/i;

function getAttr(attrs: string | undefined, name: string): string | undefined {
  if (!attrs) return undefined;
  const m = attrs.match(new RegExp(`${name}\\s*=\\s*"([^"]*)"`, "i"));
  return m?.[1];
}

function parseAllAttrs(attrs: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!attrs) return out;
  const re = /([a-zA-Z_][\w-]*)\s*=\s*"([^"]*)"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(attrs))) out[m[1]] = m[2];
  return out;
}

function parsePlanItems(raw: string): string[] {
  return raw
    .split("\n")
    .map((l) => l.replace(/^\s*(?:\d+\.|[-*])\s*(?:\[[ xX]\]\s*)?/, "").trim())
    .filter(Boolean);
}

export function parseAgentStream(raw: string): AgentSegment[] {
  const out: AgentSegment[] = [];
  let rest = raw;

  while (rest.length) {
    const m = rest.match(TAG_RE);
    if (!m) {
      pushText(out, rest);
      break;
    }
    const before = rest.slice(0, m.index!);
    if (before) pushText(out, before);
    const tag = m[0];
    const openName = m[1]?.toLowerCase();
    const closeName = m[3]?.toLowerCase();
    rest = rest.slice(m.index! + tag.length);

    if (openName) {
      const closeRe = new RegExp(`</${openName}>`, "i");
      const cm = rest.match(closeRe);
      const inner = cm ? rest.slice(0, cm.index!) : rest;
      const closed = !!cm;
      const attrs = m[2];
      if (openName === "think") {
        out.push({ kind: "think", text: inner, closed });
      } else if (openName === "plan") {
        out.push({ kind: "plan", items: parsePlanItems(inner), closed });
      } else if (openName === "followup") {
        out.push({ kind: "followup", items: parsePlanItems(inner), closed });
      } else if (openName === "chart") {
        const t = (getAttr(attrs, "type") || "line").toLowerCase();
        const chartType = (["line", "bar", "area", "pie"].includes(t)
          ? t
          : "line") as "line" | "bar" | "area" | "pie";
        out.push({
          kind: "chart",
          chartType,
          title: getAttr(attrs, "title"),
          text: inner,
          closed,
        });
      } else if (openName === "tool") {
        const all = parseAllAttrs(attrs);
        out.push({
          kind: "tool",
          toolName: all.name || "query_sql",
          toolId: all.id || `t_${out.length}`,
          args: all,
          text: inner,
          closed,
        });
      } else if (openName === "testcases") {
        const items = parseTestCases(inner);
        out.push({
          kind: "testcases",
          module: getAttr(attrs, "module"),
          title: getAttr(attrs, "title"),
          items,
          raw: inner,
          closed,
        });
      } else {
        out.push({
          kind: "step",
          title: getAttr(attrs, "title") || "分析步骤",
          source: getAttr(attrs, "source"),
          text: inner,
          closed,
        });
      }
      rest = cm ? rest.slice(cm.index! + cm[0].length) : "";
    } else if (closeName) {
      // stray close — ignore
    }
  }

  return out;
}

function parseTestCases(raw: string): TestCaseItem[] {
  const trimmed = raw.trim();
  if (!trimmed) return [];
  // Try JSON array first
  try {
    const start = trimmed.indexOf("[");
    const end = trimmed.lastIndexOf("]");
    if (start >= 0 && end > start) {
      const parsed = JSON.parse(trimmed.slice(start, end + 1));
      if (Array.isArray(parsed)) {
        return parsed
          .filter((x) => x && typeof x === "object")
          .map((x, i) => ({
            id: String(x.id ?? `TC-${i + 1}`),
            title: String(x.title ?? "未命名用例"),
            priority: x.priority ? String(x.priority) : undefined,
            type: x.type ? String(x.type) : undefined,
            preconditions: toStringArray(x.preconditions),
            steps: toStringArray(x.steps),
            expected: toStringArray(x.expected),
            data: x.data ? String(x.data) : undefined,
            linked_defect: x.linked_defect ? String(x.linked_defect) : undefined,
            linked_req: x.linked_req ? String(x.linked_req) : undefined,
            rationale: x.rationale ? String(x.rationale) : undefined,
            tags: toStringArray(x.tags),
          }));
      }
    }
  } catch {
    /* fall through */
  }
  return [];
}

function toStringArray(v: unknown): string[] | undefined {
  if (!v) return undefined;
  if (Array.isArray(v)) return v.map((x) => String(x));
  if (typeof v === "string") return [v];
  return undefined;
}

function pushText(out: AgentSegment[], text: string) {
  if (!text) return;
  const last = out[out.length - 1];
  if (last && last.kind === "text") last.text += text;
  else out.push({ kind: "text", text });
}

// Extract tool calls that the agent emitted in the assistant message.
export function extractToolCalls(raw: string) {
  return parseAgentStream(raw).filter(
    (s): s is Extract<AgentSegment, { kind: "tool" }> => s.kind === "tool" && s.closed,
  );
}

// Convert segments back to plain text (used for copy-to-clipboard).
export function segmentsToPlainText(segs: AgentSegment[]): string {
  return segs
    .filter((s) => s.kind === "text")
    .map((s) => (s as { text: string }).text.replace(/<\/?cite[^>]*>/gi, ""))
    .join("")
    .trim();
}
