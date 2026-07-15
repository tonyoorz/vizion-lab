import { defineMcp } from "@lovable.dev/mcp-js";
import searchKnowledge from "./tools/search-knowledge";
import listOntology from "./tools/list-ontology";
import ingestKnowledge from "./tools/ingest-knowledge";
import resolveConcepts from "./tools/resolve-concepts";
import getEntitySchema from "./tools/get-entity-schema";
import findJoinPathTool from "./tools/find-join-path";

export default defineMcp({
  name: "dtsv-mcp",
  title: "DTSV Analytics MCP",
  version: "0.2.0",
  instructions:
    "Tools for the DTSV testing-analytics app. Recommended flow for data Q&A: (1) `resolve_concepts` to parse the user question into entities/attributes/metrics/time; (2) `get_entity_schema` for each involved entity to ground column names and enums; (3) `find_join_path` when multiple entities are needed; (4) `search_knowledge` for supporting context; (5) then generate SQL. Use `list_ontology` for a full dump and `ingest_knowledge` to add new RAG chunks.",
  tools: [
    listOntology,
    resolveConcepts,
    getEntitySchema,
    findJoinPathTool,
    searchKnowledge,
    ingestKnowledge,
  ],
});
