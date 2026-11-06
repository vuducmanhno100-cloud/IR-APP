import { Background, Controls, MarkerType, ReactFlow, type Edge } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { call, engine, type Schemas } from "../api/client";
import { useLoad } from "../api/useLoad";
import { Badge, EmptyState, ErrorView, Loading, Section, Time } from "../components/common";
import { layoutTree, mergeTrace, NODE_HEIGHT } from "../tree/layout";
import { ProcessNodeView, type ProcessFlowNode } from "../tree/ProcessNodeView";

const nodeTypes = { process: ProcessNodeView };
const COMPLETENESS_TONE = { COMPLETE: "ok", MISSING_PARENT: "warn", TRUNCATED: "info", PENDING_DATA: "info" } as const;
const LEVEL_STYLE: Record<string, { stroke: string; strokeDasharray?: string }> = {
  VERIFIED: { stroke: "#0b6e75" },
  STRONG: { stroke: "#b7791f", strokeDasharray: "6 4" },
  WEAK: { stroke: "#9aa8b5", strokeDasharray: "2 4" },
};

export function ProcessTree({ eventId, onOpenEvent }: { eventId: number; onOpenEvent: (id: number) => void }) {
  const [processId, setProcessId] = useState<number>();
  const [includeInferred, setIncludeInferred] = useState(false);
  const trace = useLoad(async () => {
    const c = await engine();
    return call(
      c.GET("/trace/event/{event_id}", {
        params: { path: { event_id: eventId }, query: { process_id: processId, include_inferred: includeInferred } },
      }),
    );
  }, [eventId, processId, includeInferred]);
  const [view, setView] = useState<Schemas["TraceResult"]>();
  const [layout, setLayout] = useState<{ positions: Map<number, { x: number; y: number }>; version: number }>({
    positions: new Map(),
    version: 0,
  });

  useEffect(() => setView(trace.data), [trace.data]);
  useEffect(() => {
    if (view) layoutTree(view.nodes, view.edges).then((positions) => setLayout((l) => ({ positions, version: l.version + 1 })));
  }, [view]);

  const expand = useCallback(
    async (id: number) => {
      const c = await engine();
      const extra = await call(c.GET("/trace/expand/{process_id}", { params: { path: { process_id: id } } }));
      setView((current) => (current ? mergeTrace(current, extra) : extra));
    },
    [],
  );

  const matchedProcesses = useMemo(() => {
    const matched = new Set(view?.matched_event_ids ?? []);
    return new Set((view?.related_events ?? []).filter((e) => matched.has(e.event_id)).map((e) => e.process_id));
  }, [view]);

  const nodes: ProcessFlowNode[] = (view?.nodes ?? []).map((p) => ({
    id: String(p.id),
    type: "process",
    position: layout.positions.get(p.id) ?? { x: 0, y: p.depth * (NODE_HEIGHT + 40) },
    data: { process: p, matched: matchedProcesses.has(p.id), focus: p.id === view?.focus_process_id, onExpand: expand },
  }));
  const edges: Edge[] = (view?.edges ?? []).map((e) => ({
    id: `e${e.id}`,
    source: String(e.parent_id),
    target: String(e.child_id),
    label: e.level === "VERIFIED" ? undefined : e.level,
    style: LEVEL_STYLE[e.level],
    markerEnd: { type: MarkerType.ArrowClosed },
  }));

  return (
    <Section
      title={`Process Tree — event #${eventId}`}
      actions={
        <label className="inline">
          <input type="checkbox" checked={includeInferred} onChange={(e) => setIncludeInferred(e.target.checked)} />
          Hiện quan hệ suy luận (STRONG/WEAK)
        </label>
      }
    >
      <Loading when={trace.loading} />
      <ErrorView error={trace.error} />
      {view && view.candidates.length > 0 && (
        <div className="notice">
          Event liên kết với nhiều process, chọn một:
          {view.candidates.map((c) => (
            <button key={c.id} onClick={() => setProcessId(c.id)}>
              {c.name} (PID {c.pid})
            </button>
          ))}
        </div>
      )}
      {view && (
        <>
          <div className="row">
            <Badge tone={COMPLETENESS_TONE[view.completeness]}>{view.completeness}</Badge>
            {view.completeness === "MISSING_PARENT" && <span className="muted">Incomplete: missing parent events</span>}
            {view.warnings.map((w) => (
              <Badge key={w} tone="warn">
                {w}
              </Badge>
            ))}
          </div>
          <div className="tree">
            {view.nodes.every((n) => layout.positions.has(n.id)) && (
              <ReactFlow
                key={layout.version}
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                fitView
                fitViewOptions={{ padding: 0.15 }}
                nodesDraggable={false}
                minZoom={0.2}
              >
                <Background />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
          </div>
          {!view.nodes.length && <EmptyState title="Không có process">Trace không trả về process nào cho event này.</EmptyState>}
          <h3>Event liên quan</h3>
          {!view.related_events.length && <EmptyState title="Không có event liên quan">Không tìm thấy event con hoặc cha trong cửa sổ hiện tại.</EmptyState>}
          <table>
            <thead>
              <tr>
                <th>Thời gian</th>
                <th>Loại</th>
                <th>Process</th>
                <th>Nội dung</th>
              </tr>
            </thead>
            <tbody>
              {view.related_events.map((e) => (
                <tr
                  key={`${e.event_id}-${e.process_id}`}
                  className={view.matched_event_ids.includes(e.event_id) ? "hl" : ""}
                  onClick={() => onOpenEvent(e.event_id)}
                >
                  <td>
                    <Time value={e.event_time} />
                  </td>
                  <td>{e.link_type}</td>
                  <td>{view.nodes.find((n) => n.id === e.process_id)?.name ?? e.process_id}</td>
                  <td>{e.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Section>
  );
}
