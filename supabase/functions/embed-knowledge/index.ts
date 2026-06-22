// Ingest knowledge items into pgvector-backed `knowledge_chunks`.
// Body: { items: [{ source_type, source_id, title?, content, metadata? }] }

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";
import { corsHeaders, jsonResponse } from "../_shared/cors.ts";
import { chunkText, embedTexts, MODEL_VERSION } from "../_shared/embedding.ts";

interface Item {
  source_type: string;
  source_id: string;
  title?: string;
  content: string;
  metadata?: Record<string, unknown>;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return jsonResponse({ error: "POST only" }, 405);

  const apiKey = Deno.env.get("LOVABLE_API_KEY");
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!apiKey || !supabaseUrl || !serviceKey) {
    return jsonResponse({ error: "Missing env: LOVABLE_API_KEY / SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY" }, 500);
  }

  let body: { items?: Item[] };
  try {
    body = await req.json();
  } catch {
    return jsonResponse({ error: "Invalid JSON" }, 400);
  }
  const items = body.items ?? [];
  if (!Array.isArray(items) || items.length === 0) {
    return jsonResponse({ error: "items[] required" }, 400);
  }
  if (items.length > 200) return jsonResponse({ error: "max 200 items per call" }, 400);

  // Expand each item into chunks.
  type ChunkRow = {
    source_type: string;
    source_id: string;
    chunk_index: number;
    title: string | null;
    content: string;
    metadata: Record<string, unknown>;
    model_version: string;
    embedding: number[];
  };

  const expanded: { row: Omit<ChunkRow, "embedding">; text: string }[] = [];
  for (const it of items) {
    if (!it.source_type || !it.source_id || !it.content) continue;
    const chunks = chunkText(it.content);
    chunks.forEach((c, idx) => {
      expanded.push({
        row: {
          source_type: it.source_type,
          source_id: it.source_id,
          chunk_index: idx,
          title: it.title ?? null,
          content: c,
          metadata: it.metadata ?? {},
          model_version: MODEL_VERSION,
        },
        text: it.title ? `${it.title}\n\n${c}` : c,
      });
    });
  }

  if (expanded.length === 0) return jsonResponse({ inserted: 0, message: "no valid chunks" });

  const t0 = performance.now();
  let embeddings: number[][];
  try {
    embeddings = await embedTexts(apiKey, expanded.map((e) => e.text));
  } catch (err) {
    return jsonResponse({ error: (err as Error).message }, 502);
  }
  const embedMs = Math.round(performance.now() - t0);

  const rows: ChunkRow[] = expanded.map((e, i) => ({ ...e.row, embedding: embeddings[i] }));

  const supabase = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false } });
  const { error, count } = await supabase
    .from("knowledge_chunks")
    .upsert(rows, { onConflict: "source_type,source_id,chunk_index", count: "exact" });

  if (error) return jsonResponse({ error: error.message }, 500);

  return jsonResponse({
    inserted: count ?? rows.length,
    chunks: rows.length,
    embed_ms: embedMs,
    model_version: MODEL_VERSION,
  });
});
