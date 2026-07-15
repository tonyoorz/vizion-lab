import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runEval, type EvalItem } from "../../eval/runOntologyEval";

// Regression gate: current ontology-eval.json baselines. Raise these as the
// resolver improves; lowering requires an explicit review.
const THRESHOLDS = {
  entityRecall: 0.85,
  metricRecall: 0.9,
  attributeRecall: 0.7,
  timeAccuracy: 0.9,
};

const items = JSON.parse(
  readFileSync(resolve(__dirname, "../../eval/ontology-eval.json"), "utf8"),
).items as EvalItem[];

describe("ontology resolver eval", () => {
  const summary = runEval(items);

  it(`entity recall >= ${THRESHOLDS.entityRecall}`, () => {
    expect(summary.entity.r).toBeGreaterThanOrEqual(THRESHOLDS.entityRecall);
  });
  it(`metric recall >= ${THRESHOLDS.metricRecall}`, () => {
    expect(summary.metric.r).toBeGreaterThanOrEqual(THRESHOLDS.metricRecall);
  });
  it(`attribute recall >= ${THRESHOLDS.attributeRecall}`, () => {
    expect(summary.attribute.r).toBeGreaterThanOrEqual(THRESHOLDS.attributeRecall);
  });
  it(`time-hint accuracy >= ${THRESHOLDS.timeAccuracy}`, () => {
    expect(summary.timeAccuracy).toBeGreaterThanOrEqual(THRESHOLDS.timeAccuracy);
  });
});
