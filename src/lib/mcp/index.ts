import { defineMcp, auth } from "@lovable.dev/mcp-js";
import searchKnowledge from "./tools/search-knowledge";
import listOntology from "./tools/list-ontology";
import ingestKnowledge from "./tools/ingest-knowledge";
import resolveConcepts from "./tools/resolve-concepts";
import getEntitySchema from "./tools/get-entity-schema";
import findJoinPathTool from "./tools/find-join-path";

const SUPABASE_URL =
  (typeof process !== "undefined" && process.env?.SUPABASE_URL) ||
  "https://muyrtwybdfjbglnbfeis.supabase.co";

export default defineMcp({
  name: "dtsv-mcp",
  title: "DTSV Analytics MCP",
  version: "0.2.0",
  instructions:
    "Tools for the DTSV testing-analytics app. Recommended flow for data Q&A: (1) `resolve_concepts` to parse the user question into entities/attributes/metrics/time; (2) `get_entity_schema` for each involved entity to ground column names and enums; (3) `find_join_path` when multiple entities are needed; (4) `search_knowledge` for supporting context; (5) then generate SQL. Use `list_ontology` for a full dump and `ingest_knowledge` to add new RAG chunks.",
  // Require a valid Supabase-issued JWT (aud: "authenticated") on every MCP
  // request. This closes the previously-open MCP endpoint so anonymous
  // internet callers can no longer invoke `search_knowledge` (bulk read of
  // knowledge_chunks) or `ingest_knowledge` (write to the RAG index).
  auth: auth.oauth.issuer({
    issuer: `${SUPABASE_URL}/auth/v1`,
    jwksUri: `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`,
    acceptedAudiences: "authenticated",
    resourceName: "DTSV Analytics MCP",
  }),
  tools: [
    listOntology,
    resolveConcepts,
    getEntitySchema,
    findJoinPathTool,
    searchKnowledge,
    ingestKnowledge,
  ],
});
