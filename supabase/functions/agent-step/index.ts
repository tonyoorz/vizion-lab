// Thin LLM step runner for the multi-agent data-Q&A orchestrator.
// Client sends {role, systemPrompt, userPrompt, model?} → server returns {content}.
// Non-streaming: orchestration & UI stepping happen on the client.

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const DEFAULT_MODEL = "google/gemini-3-flash-preview";

interface Body {
  role: string;
  systemPrompt: string;
  userPrompt: string;
  model?: string;
  jsonMode?: boolean;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  const apiKey = Deno.env.get("LOVABLE_API_KEY");
  if (!apiKey) {
    return new Response(JSON.stringify({ error: "Missing LOVABLE_API_KEY" }), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  let body: Body;
  try {
    body = (await req.json()) as Body;
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  if (!body.systemPrompt || !body.userPrompt || !body.role) {
    return new Response(JSON.stringify({ error: "role, systemPrompt, userPrompt required" }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }

  const payload: Record<string, unknown> = {
    model: body.model || DEFAULT_MODEL,
    messages: [
      { role: "system", content: body.systemPrompt },
      { role: "user", content: body.userPrompt },
    ],
    stream: false,
  };
  if (body.jsonMode) {
    payload.response_format = { type: "json_object" };
  }

  const resp = await fetch("https://ai.gateway.lovable.dev/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(payload),
  });

  if (!resp.ok) {
    const text = await resp.text();
    return new Response(
      JSON.stringify({ error: `Gateway ${resp.status}: ${text.slice(0, 500)}` }),
      { status: resp.status, headers: { ...corsHeaders, "Content-Type": "application/json" } },
    );
  }

  const data = await resp.json();
  const content = data?.choices?.[0]?.message?.content ?? "";
  return new Response(
    JSON.stringify({ role: body.role, content, usage: data?.usage }),
    { headers: { ...corsHeaders, "Content-Type": "application/json" } },
  );
});
