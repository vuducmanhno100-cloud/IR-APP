import ELK from "elkjs/lib/elk.bundled.js";
import type { Schemas } from "../api/client";

export const NODE_WIDTH = 260;
export const NODE_HEIGHT = 76;

const elk = new ELK();

export async function layoutTree(
  nodes: Schemas["ProcessNode"][],
  edges: Schemas["ProcessEdge"][],
): Promise<Map<number, { x: number; y: number }>> {
  const ids = new Set(nodes.map((n) => n.id));
  const graph = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.spacing.nodeNode": "32",
      "elk.layered.spacing.nodeNodeBetweenLayers": "56",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    },
    children: nodes.map((n) => ({ id: String(n.id), width: NODE_WIDTH, height: NODE_HEIGHT })),
    edges: edges
      .filter((e) => ids.has(e.parent_id) && ids.has(e.child_id))
      .map((e) => ({ id: `e${e.id}`, sources: [String(e.parent_id)], targets: [String(e.child_id)] })),
  });
  const positions = new Map<number, { x: number; y: number }>();
  for (const child of graph.children ?? []) {
    positions.set(Number(child.id), { x: child.x ?? 0, y: child.y ?? 0 });
  }
  return positions;
}

export function mergeTrace(base: Schemas["TraceResult"], extra: Schemas["TraceResult"]): Schemas["TraceResult"] {
  const nodes = new Map(base.nodes.map((n) => [n.id, n]));
  for (const n of extra.nodes) {
    if (!nodes.has(n.id)) nodes.set(n.id, n);
  }
  const expandedRoot = extra.focus_process_id;
  if (expandedRoot !== null && nodes.has(expandedRoot)) {
    nodes.set(expandedRoot, { ...nodes.get(expandedRoot)!, collapsed_children: 0 });
  }
  const edges = new Map(base.edges.map((e) => [e.id, e]));
  for (const e of extra.edges) edges.set(e.id, e);
  return {
    ...base,
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    related_events: [...base.related_events, ...extra.related_events],
  };
}
