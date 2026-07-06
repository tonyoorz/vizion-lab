import { defineTool } from "@lovable.dev/mcp-js";
import { z } from "zod";
import { ONTOLOGY } from "@/lib/ontology";

export default defineTool({
  name: "list_ontology",
  title: "List DTSV ontology",
  description:
    "Return the DTSV testing-domain ontology: entities (with physical table names, attributes, enum values, synonyms) and derived metrics. Use to ground SQL/analysis over the DTSV schema.",
  inputSchema: {
    entity: z
      .string()
      .optional()
      .describe("Optional entity name (e.g. Defect, TestCase, TestRun) to return only one entity."),
  },
  annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  handler: ({ entity }) => {
    const payload = entity
      ? {
          version: ONTOLOGY.version,
          entities: ONTOLOGY.entities.filter((e) => e.name.toLowerCase() === entity.toLowerCase()),
          metrics: ONTOLOGY.metrics ?? [],
        }
      : ONTOLOGY;
    return {
      content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
      structuredContent: payload as unknown as Record<string, unknown>,
    };
  },
});
