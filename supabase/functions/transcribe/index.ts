// Speech-to-text via Lovable AI Gateway (Gemini multimodal audio)
const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }
  try {
    const { audio, mime } = await req.json();
    if (!audio || typeof audio !== "string") {
      return new Response(JSON.stringify({ error: "缺少音频数据" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }
    const LOVABLE_API_KEY = Deno.env.get("LOVABLE_API_KEY");
    if (!LOVABLE_API_KEY) throw new Error("LOVABLE_API_KEY not configured");

    const audioMime = mime || "audio/webm";
    // Derive OpenAI-style format token from mime, default to webm
    const fmt = audioMime.includes("wav")
      ? "wav"
      : audioMime.includes("mp3") || audioMime.includes("mpeg")
        ? "mp3"
        : audioMime.includes("ogg")
          ? "ogg"
          : "webm";

    const resp = await fetch(
      "https://ai.gateway.lovable.dev/v1/chat/completions",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${LOVABLE_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model: "google/gemini-2.5-flash",
          messages: [
            {
              role: "system",
              content:
                "You are a high-accuracy transcription engine. Return ONLY the verbatim transcript of the audio in its original language. No commentary, no quotes, no labels.",
            },
            {
              role: "user",
              content: [
                { type: "text", text: "Transcribe the following audio." },
                {
                  type: "input_audio",
                  input_audio: { data: audio, format: fmt },
                },
              ],
            },
          ],
        }),
      },
    );

    if (!resp.ok) {
      const t = await resp.text();
      console.error("transcribe gateway error", resp.status, t);
      const msg =
        resp.status === 429
          ? "请求过于频繁，请稍后再试。"
          : resp.status === 402
            ? "AI 额度不足。"
            : "转写失败";
      return new Response(JSON.stringify({ error: msg }), {
        status: resp.status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const data = await resp.json();
    const text: string = data?.choices?.[0]?.message?.content ?? "";
    return new Response(JSON.stringify({ text: text.trim() }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (e) {
    console.error("transcribe error", e);
    return new Response(
      JSON.stringify({ error: e instanceof Error ? e.message : "Unknown" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } },
    );
  }
});
