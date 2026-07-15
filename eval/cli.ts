// CLI: bun run eval/cli.ts  (or npx tsx eval/cli.ts)
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { runEval, type EvalItem } from "./runOntologyEval";

const here = dirname(fileURLToPath(import.meta.url));
const dataset = JSON.parse(readFileSync(resolve(here, "ontology-eval.json"), "utf8"));
const items = dataset.items as EvalItem[];
const s = runEval(items);

const pct = (x: number) => (x * 100).toFixed(1) + "%";
console.log(`\nDTSV Ontology Eval — ${s.n} items\n`);
console.log(`Entity     P=${pct(s.entity.p)}  R=${pct(s.entity.r)}`);
console.log(`Attribute  P=${pct(s.attribute.p)}  R=${pct(s.attribute.r)}`);
console.log(`Metric     P=${pct(s.metric.p)}  R=${pct(s.metric.r)}`);
console.log(`Time hint  accuracy=${pct(s.timeAccuracy)}\n`);
console.log("By tag:");
for (const [t, b] of Object.entries(s.byTag)) {
  console.log(`  ${t.padEnd(12)} n=${b.n}  entityR=${pct(b.entityR)}  metricR=${pct(b.metricR)}`);
}

const failed = s.reports.filter(
  (r) => r.entityR < 1 || r.metricR < 1 || !r.timeOk || r.attrR < 0.5,
);
if (failed.length) {
  console.log(`\n${failed.length} items below threshold:`);
  for (const f of failed) {
    console.log(`  [${f.id}] ${f.q}`);
    if (f.missing.entities.length) console.log(`     miss entities: ${f.missing.entities.join(", ")}`);
    if (f.missing.attributes.length) console.log(`     miss attrs:    ${f.missing.attributes.join(", ")}`);
    if (f.missing.metrics.length) console.log(`     miss metrics:  ${f.missing.metrics.join(", ")}`);
    if (!f.timeOk) console.log(`     time hint mismatch`);
  }
}
