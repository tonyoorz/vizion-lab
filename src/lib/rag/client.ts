// Thin client for the Hybrid RAG edge functions.
import { ONTOLOGY } from "@/lib/ontology";

const baseUrl = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1`;
const authHeader = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
});

export interface KnowledgeHit {
  id: string;
  source_type: string;
  source_id: string;
  title: string | null;
  content: string;
  metadata: Record<string, unknown>;
  vec_score: number;
  kw_score: number;
  rrf_score: number;
}

export interface SearchResult {
  query: string;
  top_k: number;
  embed_ms: number;
  search_ms: number;
  hits: KnowledgeHit[];
}

export async function searchKnowledge(
  query: string,
  opts: { topK?: number; sourceTypes?: string[]; filter?: Record<string, unknown> } = {},
): Promise<SearchResult> {
  const resp = await fetch(`${baseUrl}/hybrid-search`, {
    method: "POST",
    headers: authHeader(),
    body: JSON.stringify({
      query,
      top_k: opts.topK ?? 6,
      source_types: opts.sourceTypes,
      filter: opts.filter,
    }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data?.error || "hybrid-search failed");
  return data as SearchResult;
}

export interface IngestItem {
  source_type: string;
  source_id: string;
  title?: string;
  content: string;
  metadata?: Record<string, unknown>;
}

export async function ingestKnowledge(items: IngestItem[]) {
  const resp = await fetch(`${baseUrl}/embed-knowledge`, {
    method: "POST",
    headers: authHeader(),
    body: JSON.stringify({ items }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data?.error || "embed-knowledge failed");
  return data as { inserted: number; chunks: number; embed_ms: number; model_version: string };
}

export async function seedOntology() {
  const resp = await fetch(`${baseUrl}/seed-ontology-knowledge`, {
    method: "POST",
    headers: authHeader(),
    body: JSON.stringify({ ontology: ONTOLOGY }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data?.error || "seed failed");
  return data as { seeded_items: number; embed_result: unknown };
}

export function summarizeHitsForPrompt(hits: KnowledgeHit[], max = 6): string {
  if (!hits.length) return "(知识库无召回)";
  return hits.slice(0, max).map((h, i) => {
    const head = `#${i + 1} [${h.source_type}:${h.source_id}] rrf=${h.rrf_score.toFixed(4)}`;
    const body = (h.title ? `${h.title}\n` : "") + h.content.slice(0, 400);
    return `${head}\n${body}`;
  }).join("\n---\n");
}
