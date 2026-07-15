import { defineTool } from "@lovable.dev/mcp-js";
import { z } from "zod";
import { resolveQuestion, summarizeMatchForPrompt } from "../../ontology";

export default defineTool({
  name: "resolve_concepts",
  title: "Resolve NL → ontology concepts",
  description:
    "Parse a natural-language question (zh/en) into DTSV ontology matches: entities, attributes (with matched enum values), metrics, and time hints. Use this BEFORE writing SQL to ground column names, enums, and joins.",
  inputSchema: {
    question: z.string().min(1).describe("Raw user question in Chinese or English."),
  },
  annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  handler: ({ question }) => {
    const match = resolveQuestion(question);
    const compact = {
      entities: match.entities.map((e) => ({
        name: e.entity.name,
        table: e.entity.table,
        primaryKey: e.entity.primaryKey,
        matchedSynonym: e.matchedSynonym,
        score: e.score,
      })),
      attributes: match.attributes.map((a) => ({
        entity: a.entity.name,
        attribute: a.attribute.name,
        column: `${a.entity.table}.${a.attribute.name}`,
        type: a.attribute.type,
        enumValueMatched: a.enumValueMatched,
        matchedSynonym: a.matchedSynonym,
        score: a.score,
      })),
      metrics: match.metrics.map((m) => ({
        name: m.metric.name,
        baseEntity: m.metric.baseEntity,
        formula: m.metric.formula,
        dimensions: m.metric.dimensions,
      })),
      timeHints: match.timeHints,
    };
    return {
      content: [{ type: "text", text: summarizeMatchForPrompt(match) }],
      structuredContent: compact,
    };
  },
});
