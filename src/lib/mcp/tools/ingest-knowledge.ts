import { defineTool } from "@lovable.dev/mcp-js";
import { z } from "zod";

export default defineTool({
  name: "ingest_knowledge",
  title: "Ingest knowledge chunks",
  description:
    "Add one or more knowledge chunks (ontology notes, defect summaries, test cases, Q&A) to the DTSV RAG index. Content is embedded and stored for future hybrid search.",
  inputSchema: {
    items: z
      .array(
        z.object({
          source_type: z.enum(["ontology", "defect", "testcase", "qa_history", "qa_negative"]),
          source_id: z.string().min(1),
          title: z.string().optional(),
          content: z.string().min(1),
          metadata: z.record(z.string(), z.unknown()).optional(),
        }),
      )
      .min(1)
      .max(50),
  },
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false },
  handler: async ({ items }) => {
    const supabaseUrl = process.env.SUPABASE_URL!;
    const anonKey = process.env.SUPABASE_PUBLISHABLE_KEY ?? process.env.SUPABASE_ANON_KEY!;
    const resp = await fetch(`${supabaseUrl}/functions/v1/embed-knowledge`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${anonKey}`,
      },
      body: JSON.stringify({ items }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      return {
        content: [{ type: "text", text: data?.error ?? `embed-knowledge failed (${resp.status})` }],
        isError: true,
      };
    }
    return {
      content: [{ type: "text", text: `Inserted ${data.inserted} rows (${data.chunks} chunks) in ${data.embed_ms}ms.` }],
      structuredContent: data,
    };
  },
});
