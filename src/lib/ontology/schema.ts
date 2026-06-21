// Ontology schema — business semantic layer above raw tables.
// Lightweight TS-first definitions (no YAML runtime dep).

export type AttrType = "string" | "number" | "date" | "enum" | "boolean";

export interface Attribute {
  name: string;            // canonical column name
  label: string;           // human label (zh)
  type: AttrType;
  synonyms?: string[];     // user phrases → this attr
  values?: string[];       // for enum
}

export interface Relation {
  name: string;            // e.g. "belongs_to_module"
  target: string;          // target entity name
  via: string;             // foreign-key column on this entity
  cardinality?: "one" | "many";
}

export interface Entity {
  name: string;            // canonical PascalCase
  label: string;           // zh label
  table: string;           // physical DuckDB table
  primaryKey: string;
  synonyms: string[];      // 缺陷, bug, ...
  attributes: Attribute[];
  relations?: Relation[];
}

export interface Metric {
  name: string;            // canonical name (zh allowed)
  label: string;
  description: string;
  synonyms: string[];
  /** DuckDB SQL fragment, may reference {{table}} and {{where}} placeholders */
  formula: string;
  baseEntity: string;      // entity name to anchor the FROM clause
  dimensions: string[];    // allowed group-by dims (attribute names)
}

export interface Ontology {
  version: string;
  entities: Entity[];
  metrics: Metric[];
}

export interface OntologyMatch {
  entities: { entity: Entity; score: number; matchedSynonym?: string }[];
  attributes: {
    entity: Entity;
    attribute: Attribute;
    score: number;
    matchedSynonym?: string;
    enumValueMatched?: string;
  }[];
  metrics: { metric: Metric; score: number; matchedSynonym?: string }[];
  timeHints: { rangeDays?: number; rawPhrase?: string }[];
}
