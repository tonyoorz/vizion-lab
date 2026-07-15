// Pure evaluator for ontology resolution. Runs in Node (script) or vitest.
import { resolveQuestion } from "../src/lib/ontology";
import type { OntologyMatch } from "../src/lib/ontology/schema";

export interface EvalItem {
  id: string;
  q: string;
  tags: string[];
  entities: string[];
  attributes: string[]; // "Entity.attr"
  metrics: string[];
  timeRangeDays?: number;
  note?: string;
}

export interface EvalReport {
  id: string;
  q: string;
  tags: string[];
  entityP: number;
  entityR: number;
  attrP: number;
  attrR: number;
  metricP: number;
  metricR: number;
  timeOk: boolean;
  missing: { entities: string[]; attributes: string[]; metrics: string[] };
  extra: { entities: string[]; attributes: string[]; metrics: string[] };
}

const prf = (predicted: string[], expected: string[]) => {
  const P = new Set(predicted);
  const E = new Set(expected);
  if (E.size === 0 && P.size === 0) return { p: 1, r: 1, missing: [], extra: [] };
  const tp = [...P].filter((x) => E.has(x)).length;
  const p = P.size === 0 ? (E.size === 0 ? 1 : 0) : tp / P.size;
  const r = E.size === 0 ? 1 : tp / E.size;
  return {
    p,
    r,
    missing: [...E].filter((x) => !P.has(x)),
    extra: [...P].filter((x) => !E.has(x)),
  };
};

export const extractPredicted = (m: OntologyMatch) => ({
  entities: m.entities.map((e) => e.entity.name),
  attributes: m.attributes.map((a) => `${a.entity.name}.${a.attribute.name}`),
  metrics: m.metrics.map((x) => x.metric.name),
  timeRangeDays: m.timeHints[0]?.rangeDays,
});

export const evalItem = (item: EvalItem): EvalReport => {
  const match = resolveQuestion(item.q);
  const pred = extractPredicted(match);
  // ambiguous attribute expectations (a04): accept if ANY expected attr matched
  const isAmbig = item.tags.includes("ambiguous") && item.attributes.length > 0;
  const attrExpected = isAmbig
    ? item.attributes.some((a) => pred.attributes.includes(a))
      ? pred.attributes.filter((a) => item.attributes.includes(a))
      : item.attributes.slice(0, 1)
    : item.attributes;
  const ent = prf(pred.entities, item.entities);
  const attr = prf(pred.attributes, attrExpected);
  const met = prf(pred.metrics, item.metrics);
  const timeOk =
    item.timeRangeDays === undefined
      ? true
      : pred.timeRangeDays === item.timeRangeDays;
  return {
    id: item.id,
    q: item.q,
    tags: item.tags,
    entityP: ent.p,
    entityR: ent.r,
    attrP: attr.p,
    attrR: attr.r,
    metricP: met.p,
    metricR: met.r,
    timeOk,
    missing: { entities: ent.missing, attributes: attr.missing, metrics: met.missing },
    extra: { entities: ent.extra, attributes: attr.extra, metrics: met.extra },
  };
};

export interface EvalSummary {
  n: number;
  entity: { p: number; r: number };
  attribute: { p: number; r: number };
  metric: { p: number; r: number };
  timeAccuracy: number;
  byTag: Record<string, { n: number; entityR: number; metricR: number }>;
  reports: EvalReport[];
}

export const runEval = (items: EvalItem[]): EvalSummary => {
  const reports = items.map(evalItem);
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  const byTag: EvalSummary["byTag"] = {};
  for (const r of reports) {
    for (const t of r.tags) {
      const bucket = (byTag[t] ??= { n: 0, entityR: 0, metricR: 0 });
      bucket.n += 1;
      bucket.entityR += r.entityR;
      bucket.metricR += r.metricR;
    }
  }
  for (const k of Object.keys(byTag)) {
    byTag[k].entityR /= byTag[k].n;
    byTag[k].metricR /= byTag[k].n;
  }
  return {
    n: reports.length,
    entity: { p: avg(reports.map((r) => r.entityP)), r: avg(reports.map((r) => r.entityR)) },
    attribute: { p: avg(reports.map((r) => r.attrP)), r: avg(reports.map((r) => r.attrR)) },
    metric: { p: avg(reports.map((r) => r.metricP)), r: avg(reports.map((r) => r.metricR)) },
    timeAccuracy: avg(reports.map((r) => (r.timeOk ? 1 : 0))),
    byTag,
    reports,
  };
};
