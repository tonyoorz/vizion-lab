// One-shot seeder: serialize the ontology (entities + metrics + synonyms)
// into `knowledge_chunks`. Idempotent: re-runs upsert by (source_type, source_id).
//
// Body: { ontology?: <Ontology JSON> }  — caller should POST the client-side
// ONTOLOGY object so the seeder stays in sync with src/lib/ontology/definitions.ts.

import { corsHeaders, jsonResponse } from "../_shared/cors.ts";

interface OntologyAttr { name: string; label: string; type: string; values?: string[]; synonyms?: string[]; }
interface OntologyRel { name: string; target: string; via: string; cardinality: string; }
interface OntologyEntity { name: string; label: string; table: string; primaryKey: string; synonyms?: string[]; attributes: OntologyAttr[]; relations?: OntologyRel[]; }
interface OntologyMetric { name: string; label: string; description: string; synonyms?: string[]; baseEntity: string; dimensions: string[]; formula: string; }
interface Ontology { version: string; entities: OntologyEntity[]; metrics: OntologyMetric[]; }

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return jsonResponse({ error: "POST only" }, 405);

  let body: { ontology?: Ontology };
  try { body = await req.json(); } catch { return jsonResponse({ error: "Invalid JSON" }, 400); }
  const ont = body.ontology;
  if (!ont || !Array.isArray(ont.entities)) return jsonResponse({ error: "ontology required" }, 400);

  const items: Array<{ source_type: string; source_id: string; title: string; content: string; metadata: Record<string, unknown> }> = [];

  for (const e of ont.entities) {
    const attrLines = e.attributes.map((a) => {
      const valStr = a.values?.length ? ` 取值: ${a.values.join("/")}` : "";
      const synStr = a.synonyms?.length ? ` 同义: ${a.synonyms.join("/")}` : "";
      return `- ${a.name} (${a.label}, ${a.type})${valStr}${synStr}`;
    }).join("\n");
    const relLines = (e.relations ?? []).map((r) => `- ${r.name} → ${r.target} via ${r.via} (${r.cardinality})`).join("\n");
    const content = `实体: ${e.name} / ${e.label}
物理表: ${e.table}  主键: ${e.primaryKey}
同义词: ${(e.synonyms ?? []).join(", ")}

属性:
${attrLines}

关系:
${relLines || "(无)"}`;
    items.push({
      source_type: "ontology",
      source_id: `entity:${e.name}`,
      title: `${e.label} (${e.name})`,
      content,
      metadata: { kind: "entity", table: e.table, ontology_version: ont.version },
    });
  }

  for (const m of ont.metrics) {
    const content = `指标: ${m.name} / ${m.label}
描述: ${m.description}
基实体: ${m.baseEntity}
公式: ${m.formula}
维度: ${m.dimensions.join(", ")}
同义词: ${(m.synonyms ?? []).join(", ")}`;
    items.push({
      source_type: "ontology",
      source_id: `metric:${m.name}`,
      title: `${m.label} 指标`,
      content,
      metadata: { kind: "metric", base_entity: m.baseEntity, ontology_version: ont.version },
    });
  }

  // Forward to embed-knowledge to do the actual embedding + upsert.
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  if (!supabaseUrl) return jsonResponse({ error: "Missing SUPABASE_URL" }, 500);

  const resp = await fetch(`${supabaseUrl}/functions/v1/embed-knowledge`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: req.headers.get("Authorization") ?? "",
    },
    body: JSON.stringify({ items }),
  });
  const data = await resp.json();
  return jsonResponse({ seeded_items: items.length, embed_result: data }, resp.ok ? 200 : resp.status);
});
