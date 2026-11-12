from dataclasses import dataclass
from functools import cache
from importlib import resources

import psycopg

from ..contracts import EdgeLevel

CONFIRMED = [EdgeLevel.VERIFIED.value]
WITH_INFERRED = [EdgeLevel.VERIFIED.value, EdgeLevel.STRONG.value, EdgeLevel.WEAK.value]


@cache
def sql(name: str) -> str:
    return resources.files("ir_engine.investigation.sql").joinpath(f"{name}.sql").read_text(encoding="utf-8")


@dataclass(frozen=True)
class TreeShape:
    depth_by_id: dict[int, int]
    edge_ids: set[int]
    collapsed: dict[int, int]
    truncated: bool


def tree_shape(
    conn: psycopg.Connection,
    focus_id: int,
    max_up: int = 50,
    max_depth: int = 3,
    max_nodes: int = 500,
    include_inferred: bool = False,
) -> TreeShape:
    levels = WITH_INFERRED if include_inferred else CONFIRMED
    rows_up = conn.execute(sql("ancestors"), {"process_id": focus_id, "levels": levels, "max_up": max_up}).fetchall()
    rows_down = conn.execute(
        sql("descendants"),
        {"process_id": focus_id, "levels": levels, "max_depth": max_depth, "max_nodes": max_nodes},
    ).fetchall()
    ancestor_cut = False
    if max_up > 0:
        boundary = [r["id"] for r in rows_up if r["depth"] == -max_up]
        if boundary:
            ancestor_cut = (
                conn.execute(
                    "SELECT 1 FROM process_edges WHERE child_id=ANY(%s) AND level=ANY(%s) LIMIT 1",
                    (boundary, levels),
                ).fetchone()
                is not None
            )
    rows = [dict(r) for r in rows_up] + [dict(r) for r in rows_down]
    unique = {r["id"]: r for r in rows}
    ordered = sorted(unique.values(), key=lambda r: (abs(r["depth"]), r["depth"], r["id"]))
    limited = len(ordered) > max_nodes
    selected = {r["id"] for r in ordered[:max_nodes]}
    rows_up = [r for r in rows_up if r["id"] in selected]
    rows_down = [r for r in rows_down if r["id"] in selected]
    rows = [r for r in rows if r["id"] in selected]
    depth_by_id = {r["id"]: r["depth"] for r in rows}
    edge_ids = {r["edge_id"] for r in rows if r["edge_id"] is not None}
    returned_children: dict[int, int] = {}
    for r in rows_down:
        if r["parent_id"] is not None:
            returned_children[r["parent_id"]] = returned_children.get(r["parent_id"], 0) + 1
    path_child = {r["id"]: 1 for r in rows_up if r["child_id"] is not None}
    totals = {r["id"]: r["children"] for r in conn.execute(sql("child_counts"), {"ids": list(depth_by_id), "levels": levels}).fetchall()}
    collapsed = {}
    for node_id, depth in depth_by_id.items():
        shown = returned_children.get(node_id, 0) + (path_child.get(node_id, 0) if depth < 0 else 0)
        collapsed[node_id] = max(totals.get(node_id, 0) - shown, 0)
    truncated = ancestor_cut or limited or len(rows_down) >= max_nodes or any(collapsed[n] for n, d in depth_by_id.items() if d >= 0)
    return TreeShape(depth_by_id, edge_ids, collapsed, truncated)
