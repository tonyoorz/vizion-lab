// Relation graph traversal — find JOIN path between two entities.
import { ONTOLOGY } from "./definitions";
import type { Entity } from "./schema";

interface Edge { from: string; to: string; via: string }

const buildEdges = (): Edge[] => {
  const edges: Edge[] = [];
  for (const e of ONTOLOGY.entities) {
    for (const r of e.relations ?? []) {
      edges.push({ from: e.name, to: r.target, via: r.via });
      edges.push({ from: r.target, to: e.name, via: r.via });
    }
  }
  return edges;
};

const EDGES = buildEdges();

export const findJoinPath = (from: string, to: string): Edge[] | null => {
  if (from === to) return [];
  const queue: { node: string; path: Edge[] }[] = [{ node: from, path: [] }];
  const seen = new Set<string>([from]);
  while (queue.length) {
    const { node, path } = queue.shift()!;
    for (const e of EDGES.filter((x) => x.from === node)) {
      if (seen.has(e.to)) continue;
      const np = [...path, e];
      if (e.to === to) return np;
      seen.add(e.to);
      queue.push({ node: e.to, path: np });
    }
  }
  return null;
};

export const entityByName = (name: string): Entity | undefined =>
  ONTOLOGY.entities.find((e) => e.name === name);
