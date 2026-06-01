// Extract text content from uploaded files (PDF, DOCX, PPTX, XLSX, CSV, TXT, JSON, MD)
// Returns plain text so the chat model can reason over it.
import { extractText, getDocumentProxy } from "npm:unpdf@0.12.1";
import JSZip from "npm:jszip@3.10.1";
import * as XLSX from "npm:xlsx@0.18.5";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

const MAX_CHARS = 60_000;

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function stripXml(xml: string): string {
  return xml
    .replace(/<a:p[^>]*>/g, "\n")
    .replace(/<w:p[^>]*>/g, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

async function extractPdf(bytes: Uint8Array): Promise<{ text: string; pages: number }> {
  const pdf = await getDocumentProxy(bytes);
  const { text, totalPages } = await extractText(pdf, { mergePages: true });
  return { text: Array.isArray(text) ? text.join("\n\n") : text, pages: totalPages };
}

async function extractPptx(bytes: Uint8Array): Promise<{ text: string; pages: number }> {
  const zip = await JSZip.loadAsync(bytes);
  const slideFiles = Object.keys(zip.files)
    .filter((p) => /^ppt\/slides\/slide\d+\.xml$/.test(p))
    .sort((a, b) => {
      const na = parseInt(a.match(/slide(\d+)/)?.[1] || "0");
      const nb = parseInt(b.match(/slide(\d+)/)?.[1] || "0");
      return na - nb;
    });
  const parts: string[] = [];
  for (let i = 0; i < slideFiles.length; i++) {
    const xml = await zip.files[slideFiles[i]].async("string");
    const text = stripXml(xml);
    if (text) parts.push(`# Slide ${i + 1}\n${text}`);
  }
  return { text: parts.join("\n\n"), pages: slideFiles.length };
}

async function extractDocx(bytes: Uint8Array): Promise<{ text: string; pages: number }> {
  const zip = await JSZip.loadAsync(bytes);
  const doc = zip.files["word/document.xml"];
  if (!doc) return { text: "", pages: 0 };
  const xml = await doc.async("string");
  return { text: stripXml(xml), pages: 1 };
}

function extractXlsx(bytes: Uint8Array): { text: string; pages: number } {
  const wb = XLSX.read(bytes, { type: "array" });
  const parts: string[] = [];
  for (const name of wb.SheetNames) {
    const ws = wb.Sheets[name];
    const csv = XLSX.utils.sheet_to_csv(ws);
    if (csv.trim()) parts.push(`# Sheet: ${name}\n${csv.slice(0, 20000)}`);
  }
  return { text: parts.join("\n\n"), pages: wb.SheetNames.length };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  try {
    const { name, mime, dataBase64 } = await req.json();
    if (!dataBase64 || typeof dataBase64 !== "string") {
      return new Response(JSON.stringify({ error: "缺少文件数据" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }
    const bytes = b64ToBytes(dataBase64);
    const lower = (name || "").toLowerCase();
    const m = (mime || "").toLowerCase();

    let result: { text: string; pages: number; kind: string };
    if (m.includes("pdf") || lower.endsWith(".pdf")) {
      const r = await extractPdf(bytes);
      result = { ...r, kind: "pdf" };
    } else if (lower.endsWith(".pptx") || m.includes("presentationml")) {
      const r = await extractPptx(bytes);
      result = { ...r, kind: "pptx" };
    } else if (lower.endsWith(".docx") || m.includes("wordprocessingml")) {
      const r = await extractDocx(bytes);
      result = { ...r, kind: "docx" };
    } else if (lower.endsWith(".xlsx") || lower.endsWith(".xls") || m.includes("spreadsheetml")) {
      const r = extractXlsx(bytes);
      result = { ...r, kind: "xlsx" };
    } else {
      // text-ish fallback
      const text = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
      result = { text, pages: 1, kind: "text" };
    }

    const truncated = result.text.length > MAX_CHARS;
    const text = truncated ? result.text.slice(0, MAX_CHARS) : result.text;

    return new Response(
      JSON.stringify({
        text: text.trim(),
        pages: result.pages,
        kind: result.kind,
        truncated,
        chars: text.length,
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } },
    );
  } catch (e) {
    console.error("extract-file error:", e);
    return new Response(
      JSON.stringify({ error: e instanceof Error ? e.message : "解析失败" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } },
    );
  }
});
