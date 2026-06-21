// Natural-language → ontology concept resolver.
// Two-tier matching: exact synonym → substring (case-insensitive, zh-aware).
// LLM fallback happens in the Ontologist agent, not here.

import { ONTOLOGY } from "./definitions";
import type { Attribute, Entity, Metric, OntologyMatch } from "./schema";

const norm = (s: string) => s.toLowerCase().trim();

const matchSynonym = (text: string, synonyms: string[]): string | undefined => {
  const t = norm(text);
  for (const s of synonyms) {
    if (t.includes(norm(s))) return s;
  }
  return undefined;
};

// crude time-range extraction
const TIME_PATTERNS: { re: RegExp; days: number }[] = [
  { re: /今天|today/i, days: 1 },
  { re: /昨天|yesterday/i, days: 2 },
  { re: /本周|这周|this week/i, days: 7 },
  { re: /上周|last week/i, days: 14 },
  { re: /近\s*(\d+)\s*天/, days: 0 }, // dynamic
  { re: /本月|this month/i, days: 30 },
  { re: /近一个月|过去一个月/, days: 30 },
  { re: /上个月|last month/i, days: 60 },
  { re: /近\s*90\s*天|近三个月|过去三个月/, days: 90 },
  { re: /今年|this year/i, days: 365 },
];

const extractTime = (text: string) => {
  const hints: { rangeDays?: number; rawPhrase?: string }[] = [];
  const dyn = /近\s*(\d+)\s*天/.exec(text);
  if (dyn) hints.push({ rangeDays: Number(dyn[1]), rawPhrase: dyn[0] });
  for (const p of TIME_PATTERNS) {
    if (p.days === 0) continue;
    const m = p.re.exec(text);
    if (m) hints.push({ rangeDays: p.days, rawPhrase: m[0] });
  }
  return hints;
};

export const resolveQuestion = (question: string): OntologyMatch => {
  const match: OntologyMatch = {
    entities: [],
    attributes: [],
    metrics: [],
    timeHints: extractTime(question),
  };

  for (const entity of ONTOLOGY.entities) {
    const ms = matchSynonym(question, entity.synonyms);
    if (ms) {
      match.entities.push({ entity, score: 1, matchedSynonym: ms });
    }
    for (const attr of entity.attributes) {
      const am = matchSynonym(question, attr.synonyms ?? [attr.label]);
      // also try enum value direct mention (e.g. "P0")
      let enumHit: string | undefined;
      if (attr.values) {
        for (const v of attr.values) {
          if (norm(question).includes(norm(v))) {
            enumHit = v;
            break;
          }
        }
      }
      if (am || enumHit) {
        match.attributes.push({
          entity,
          attribute: attr,
          score: enumHit ? 1 : 0.8,
          matchedSynonym: am,
          enumValueMatched: enumHit,
        });
      }
    }
  }

  for (const metric of ONTOLOGY.metrics) {
    const mm = matchSynonym(question, [metric.name, metric.label, ...metric.synonyms]);
    if (mm) {
      match.metrics.push({ metric, score: 1, matchedSynonym: mm });
      // ensure the base entity is included
      const baseEnt = ONTOLOGY.entities.find((e) => e.name === metric.baseEntity);
      if (baseEnt && !match.entities.some((x) => x.entity.name === baseEnt.name)) {
        match.entities.push({ entity: baseEnt, score: 0.9, matchedSynonym: "(by metric)" });
      }
    }
  }

  return match;
};

export const summarizeMatchForPrompt = (m: OntologyMatch): string => {
  const lines: string[] = [];
  if (m.entities.length) {
    lines.push("Entities: " + m.entities.map((x) => `${x.entity.name}(${x.entity.table})`).join(", "));
  }
  if (m.attributes.length) {
    lines.push(
      "Attributes: " +
        m.attributes
          .map(
            (x) =>
              `${x.entity.name}.${x.attribute.name}${
                x.enumValueMatched ? `=${x.enumValueMatched}` : ""
              }`,
          )
          .join(", "),
    );
  }
  if (m.metrics.length) {
    lines.push(
      "Metrics: " +
        m.metrics
          .map((x) => `${x.metric.name} [formula: ${x.metric.formula}]`)
          .join("; "),
    );
  }
  if (m.timeHints.length) {
    lines.push("Time: " + m.timeHints.map((t) => `${t.rangeDays}d (${t.rawPhrase})`).join(", "));
  }
  return lines.join("\n") || "(no direct ontology matches)";
};

export const listAllowedFields = (m: OntologyMatch): string[] => {
  const set = new Set<string>();
  for (const e of m.entities) {
    for (const a of e.entity.attributes) set.add(`${e.entity.table}.${a.name}`);
    set.add(`${e.entity.table}.${e.entity.primaryKey}`);
  }
  return Array.from(set);
};
