// Hybrid (vector + keyword) retrieval over knowledge_chunks.
// Body: { query: string, top_k?: number, source_types?: string[], filter?: object }

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";
import { corsHeaders, jsonResponse } from "../_shared/cors.ts";
import { embedTexts } from "../_shared/embedding.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return jsonResponse({ error: "POST only" }, 405);

  const apiKey = Deno.env.get("LOVABLE_API_KEY");
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!apiKey || !supabaseUrl || !serviceKey) {
    return jsonResponse({ error: "Missing env" }, 500);
  }

  let body: { query?: string; top_k?: number; source_types?: string[]; filter?: Record<string, unknown> };
  try { body = await req.json(); } catch { return jsonResponse({ error: "Invalid JSON" }, 400); }

  const query = (body.query ?? "").trim();
  if (!query) return jsonResponse({ error: "query required" }, 400);
  const topK = Math.min(Math.max(body.top_k ?? 8, 1), 30);

  const t0 = performance.now();
  let embedding: number[];
  try {
    const [vec] = await embedTexts(apiKey, [query]);
    embedding = vec;
  } catch (err) {
    return jsonResponse({ error: (err as Error).message }, 502);
  }
  const embedMs = Math.round(performance.now() - t0);

  const supabase = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false } });

  const t1 = performance.now();
  const { data, error } = await supabase.rpc("match_knowledge", {
    query_embedding: embedding,
    query_text: query,
    match_count: topK,
    source_types: body.source_types ?? null,
    filter: body.filter ?? {},
  });
  if (error) return jsonResponse({ error: error.message }, 500);
  const searchMs = Math.round(performance.now() - t1);

  return jsonResponse({
    query,
    top_k: topK,
    embed_ms: embedMs,
    search_ms: searchMs,
    hits: data ?? [],
  });
});
