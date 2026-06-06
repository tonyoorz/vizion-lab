// web_search edge function. Zero-config: uses DuckDuckGo HTML endpoint and parses anchors.
// Returns top results { title, url, snippet }.

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  try {
    const { query } = await req.json();
    if (typeof query !== "string" || !query.trim()) {
      return json({ error: "query required" }, 400);
    }
    const q = encodeURIComponent(query.trim());
    const resp = await fetch(`https://duckduckgo.com/html/?q=${q}`, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
      },
    });
    if (!resp.ok) {
      return json({ error: `upstream ${resp.status}` }, 502);
    }
    const html = await resp.text();
    const results = parseDdgHtml(html).slice(0, 8);
    return json({ query, results });
  } catch (e) {
    return json({ error: e instanceof Error ? e.message : String(e) }, 500);
  }
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

function parseDdgHtml(html: string) {
  const results: { title: string; url: string; snippet: string }[] = [];
  // DuckDuckGo HTML result blocks
  const blockRe = /<div[^>]*class="[^"]*result[^"]*"[^>]*>([\s\S]*?)<\/div>\s*<\/div>/gi;
  const linkRe = /<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/i;
  const snipRe = /<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)<\/a>/i;
  let m: RegExpExecArray | null;
  while ((m = blockRe.exec(html))) {
    const block = m[1];
    const lm = linkRe.exec(block);
    if (!lm) continue;
    let url = lm[1];
    // DDG wraps with /l/?uddg=...
    const uddg = url.match(/[?&]uddg=([^&]+)/);
    if (uddg) try { url = decodeURIComponent(uddg[1]); } catch {}
    const title = stripTags(lm[2]).trim();
    const sm = snipRe.exec(block);
    const snippet = sm ? stripTags(sm[1]).trim() : "";
    if (title && url.startsWith("http")) results.push({ title, url, snippet });
    if (results.length >= 8) break;
  }
  return results;
}

function stripTags(s: string) {
  return s.replace(/<[^>]+>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, " ");
}
