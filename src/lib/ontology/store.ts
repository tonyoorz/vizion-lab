// Ontology override store — persists user edits in localStorage and mutates
// the singleton ONTOLOGY object in-place so all consumers (resolver, graph,
// orchestrator prompts) immediately see the latest semantic layer.
import { ONTOLOGY } from "./definitions";
import type { Entity, Metric, Ontology } from "./schema";

const STORAGE_KEY = "dtsv.ontology.overrides.v1";

// Deep-clone baseline once for reset.
const BASELINE: Ontology = JSON.parse(JSON.stringify(ONTOLOGY));

type Listener = () => void;
const listeners = new Set<Listener>();

export const subscribeOntology = (fn: Listener): (() => void) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};
const emit = () => listeners.forEach((l) => l());

const writeBack = (next: Ontology) => {
  ONTOLOGY.version = next.version;
  ONTOLOGY.entities = next.entities;
  ONTOLOGY.metrics = next.metrics;
};

export const loadOverridesFromStorage = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Ontology;
    if (parsed?.entities && parsed?.metrics) writeBack(parsed);
  } catch (e) {
    console.warn("[ontology] failed to load overrides", e);
  }
};

export const persistOntology = (next: Ontology) => {
  writeBack(next);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch (e) {
    console.warn("[ontology] failed to persist", e);
  }
  emit();
};

export const resetOntology = () => {
  try { localStorage.removeItem(STORAGE_KEY); } catch {}
  writeBack(JSON.parse(JSON.stringify(BASELINE)));
  emit();
};

export const getOntologySnapshot = (): Ontology =>
  JSON.parse(JSON.stringify(ONTOLOGY));

export const updateEntity = (name: string, patch: Partial<Entity>) => {
  const snap = getOntologySnapshot();
  snap.entities = snap.entities.map((e) => (e.name === name ? { ...e, ...patch } : e));
  persistOntology(snap);
};

export const upsertMetric = (metric: Metric, originalName?: string) => {
  const snap = getOntologySnapshot();
  const key = originalName ?? metric.name;
  const idx = snap.metrics.findIndex((m) => m.name === key);
  if (idx >= 0) snap.metrics[idx] = metric;
  else snap.metrics.push(metric);
  persistOntology(snap);
};

export const deleteMetric = (name: string) => {
  const snap = getOntologySnapshot();
  snap.metrics = snap.metrics.filter((m) => m.name !== name);
  persistOntology(snap);
};

// Hydrate on module load (synchronous, happens before first render uses ontology).
loadOverridesFromStorage();
