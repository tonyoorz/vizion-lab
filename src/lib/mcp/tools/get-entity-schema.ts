import { defineTool } from "@lovable.dev/mcp-js";
import { z } from "zod";
import { ONTOLOGY, entityByName } from "../../ontology";

export default defineTool({
  name: "get_entity_schema",
  title: "Get entity schema",
  description:
    "Return the physical schema (table, primary key, columns with type/enum values/synonyms, relations) for a single ontology entity. Use to prevent column-name hallucinations before generating SQL.",
  inputSchema: {
    entity: z
      .string()
      .min(1)
      .describe("Entity name (e.g. Defect, TestCase, TestRun). Case-insensitive."),
  },
  annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  handler: ({ entity }) => {
    const found =
      entityByName(entity) ??
      ONTOLOGY.entities.find((e) => e.name.toLowerCase() === entity.toLowerCase());
    if (!found) {
      const known = ONTOLOGY.entities.map((e) => e.name).join(", ");
      return {
        content: [{ type: "text", text: `Unknown entity "${entity}". Known: ${known}` }],
        isError: true,
      };
    }
    const payload = {
      name: found.name,
      label: found.label,
      table: found.table,
      primaryKey: found.primaryKey,
      columns: found.attributes.map((a) => ({
        name: a.name,
        label: a.label,
        type: a.type,
        values: a.values,
        synonyms: a.synonyms ?? [],
      })),
      relations: found.relations ?? [],
      allowedColumns: [
        found.primaryKey,
        ...found.attributes.map((a) => a.name),
      ].map((c) => `${found.table}.${c}`),
    };
    return {
      content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
      structuredContent: payload,
    };
  },
});
