// Shared helper to call Lovable AI Gateway embeddings.
// Fixed at 1536 dims so it matches the pgvector(1536) column.

export const EMBEDDING_MODEL = "google/gemini-embedding-001";
export const EMBEDDING_DIMS = 1536;
export const MODEL_VERSION = `${EMBEDDING_MODEL}@${EMBEDDING_DIMS}`;

export async function embedTexts(apiKey: string, inputs: string[]): Promise<number[][]> {
  if (inputs.length === 0) return [];
  // Gateway accepts string or string[]. Batch to avoid huge payloads.
  const out: number[][] = [];
  const BATCH = 32;
  for (let i = 0; i < inputs.length; i += BATCH) {
    const slice = inputs.slice(i, i + BATCH);
    const resp = await fetch("https://ai.gateway.lovable.dev/v1/embeddings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Lovable-API-Key": apiKey,
      },
      body: JSON.stringify({
        model: EMBEDDING_MODEL,
        input: slice,
        dimensions: EMBEDDING_DIMS,
      }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Embedding gateway ${resp.status}: ${text.slice(0, 400)}`);
    }
    const data = await resp.json();
    for (const item of data?.data ?? []) {
      out.push(item.embedding as number[]);
    }
  }
  return out;
}

// Naive paragraph-based chunker; keeps chunks under ~1200 chars.
export function chunkText(text: string, max = 1200): string[] {
  const clean = text.replace(/\r\n/g, "\n").trim();
  if (clean.length <= max) return [clean];
  const paras = clean.split(/\n{2,}|(?<=。)\s+/g);
  const chunks: string[] = [];
  let buf = "";
  for (const p of paras) {
    if ((buf + "\n\n" + p).length > max && buf) {
      chunks.push(buf.trim());
      buf = p;
    } else {
      buf = buf ? `${buf}\n\n${p}` : p;
    }
  }
  if (buf.trim()) chunks.push(buf.trim());
  return chunks;
}
