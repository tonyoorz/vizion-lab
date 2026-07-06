import { defineMcp } from "@lovable.dev/mcp-js";
import searchKnowledge from "./tools/search-knowledge";
import listOntology from "./tools/list-ontology";
import ingestKnowledge from "./tools/ingest-knowledge";

export default defineMcp({
  name: "dtsv-mcp",
  title: "DTSV Analytics MCP",
  version: "0.1.0",
  instructions:
    "Tools for the DTSV testing-analytics app. Use `list_ontology` to discover entities/metrics, `search_knowledge` for hybrid (vector + keyword) recall over ontology/defect/testcase/Q&A chunks, and `ingest_knowledge` to add new chunks to the RAG index.",
  tools: [listOntology, searchKnowledge, ingestKnowledge],
});
