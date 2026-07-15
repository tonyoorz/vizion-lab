import { defineTool } from "@lovable.dev/mcp-js";
import { z } from "zod";
import { findJoinPath, entityByName } from "../../ontology";

export default defineTool({
  name: "find_join_path",
  title: "Find join path between entities",
  description:
    "BFS shortest join path between two ontology entities. Returns the ordered edges (from → to via foreign-key column) needed to JOIN the underlying tables. Returns an empty path if from==to and null if unreachable.",
  inputSchema: {
    from: z.string().min(1).describe("Source entity name (e.g. Defect)."),
    to: z.string().min(1).describe("Target entity name (e.g. TestCase)."),
  },
  annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  handler: ({ from, to }) => {
    const fromE = entityByName(from);
    const toE = entityByName(to);
    if (!fromE || !toE) {
      return {
        content: [{ type: "text", text: `Unknown entity: ${!fromE ? from : to}` }],
        isError: true,
      };
    }
    const path = findJoinPath(fromE.name, toE.name);
    if (path === null) {
      return {
        content: [{ type: "text", text: `No join path from ${from} to ${to}.` }],
        structuredContent: { reachable: false, path: [] },
      };
    }
    const enriched = path.map((e) => {
      const fromEnt = entityByName(e.from)!;
      const toEnt = entityByName(e.to)!;
      return {
        from: e.from,
        to: e.to,
        via: e.via,
        joinSql: `JOIN ${toEnt.table} ON ${fromEnt.table}.${e.via} = ${toEnt.table}.${e.via}`,
      };
    });
    return {
      content: [
        {
          type: "text",
          text:
            enriched.length === 0
              ? `${from} == ${to} (no join needed)`
              : enriched.map((x) => x.joinSql).join("\n"),
        },
      ],
      structuredContent: { reachable: true, path: enriched },
    };
  },
});
