import { defineTool } from "@lovable.dev/mcp-js";
import { z } from "zod";

export default defineTool({
  name: "search_knowledge",
  title: "Search DTSV knowledge base",
  description:
    "Hybrid (vector + keyword) search over the DTSV knowledge base: ontology descriptions, defect/testcase samples, and Q&A history. Returns top-k chunks with scores.",
  inputSchema: {
    query: z.string().min(1).describe("Natural-language query in Chinese or English."),
    top_k: z.number().int().min(1).max(20).default(6),
    source_types: z
      .array(z.enum(["ontology", "defect", "testcase", "qa_history", "qa_negative"]))
      .optional()
      .describe("Optional filter to restrict source types."),
  },
  annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  handler: async ({ query, top_k, source_types }) => {
    const supabaseUrl = process.env.SUPABASE_URL!;
    const anonKey = process.env.SUPABASE_PUBLISHABLE_KEY ?? process.env.SUPABASE_ANON_KEY!;
    const resp = await fetch(`${supabaseUrl}/functions/v1/hybrid-search`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${anonKey}`,
      },
      body: JSON.stringify({ query, top_k, source_types }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      return {
        content: [{ type: "text", text: data?.error ?? `hybrid-search failed (${resp.status})` }],
        isError: true,
      };
    }
    const summary = (data.hits ?? [])
      .map((h: any, i: number) =>
        `#${i + 1} [${h.source_type}:${h.source_id}] rrf=${h.rrf_score?.toFixed?.(4) ?? "-"}\n${h.title ? h.title + "\n" : ""}${(h.content ?? "").slice(0, 400)}`,
      )
      .join("\n---\n") || "(no hits)";
    return {
      content: [{ type: "text", text: summary }],
      structuredContent: data,
    };
  },
});
