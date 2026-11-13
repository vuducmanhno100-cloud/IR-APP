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

function childCounts(edges: Schemas["ProcessEdge"][]): Map<number, number> {
  const children = new Map<number, Set<number>>();
  for (const e of edges) {
    if (!children.has(e.parent_id)) children.set(e.parent_id, new Set());
    children.get(e.parent_id)!.add(e.child_id);
  }
  return new Map([...children].map(([id, set]) => [id, set.size]));
}

/**
 * Adds an expanded subtree to the current view. Each response counts the children it left out; the hidden count
 * after merging is the total number of children minus those now shown, so a subtree cut by max_nodes keeps its
 * "+N" button. Related events of processes present in both responses appear once.
 */
export function mergeTrace(base: Schemas["TraceResult"], extra: Schemas["TraceResult"]): Schemas["TraceResult"] {
  const edges = new Map(base.edges.map((e) => [e.id, e]));
  for (const e of extra.edges) edges.set(e.id, e);
  const shown = { base: childCounts(base.edges), extra: childCounts(extra.edges), merged: childCounts([...edges.values()]) };
  const extraNodes = new Map(extra.nodes.map((n) => [n.id, n]));
  const nodes = new Map(base.nodes.map((n) => [n.id, n]));
  for (const n of extra.nodes) if (!nodes.has(n.id)) nodes.set(n.id, n);
  for (const [id, node] of nodes) {
    const totals: number[] = [];
    const fromBase = base.nodes.find((n) => n.id === id);
    if (fromBase) totals.push((shown.base.get(id) ?? 0) + fromBase.collapsed_children);
    const fromExtra = extraNodes.get(id);
    if (fromExtra) totals.push((shown.extra.get(id) ?? 0) + fromExtra.collapsed_children);
    const hidden = Math.max(0, Math.max(...totals) - (shown.merged.get(id) ?? 0));
    if (hidden !== node.collapsed_children) nodes.set(id, { ...node, collapsed_children: hidden });
  }
  const related = new Map(base.related_events.map((e) => [`${e.event_id}-${e.process_id}`, e]));
  for (const e of extra.related_events) related.set(`${e.event_id}-${e.process_id}`, e);
  return {
    ...base,
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    related_events: [...related.values()].sort((a, b) => b.event_time.localeCompare(a.event_time) || b.event_id - a.event_id),
    warnings: [...new Set([...base.warnings, ...extra.warnings])],
  };
}
