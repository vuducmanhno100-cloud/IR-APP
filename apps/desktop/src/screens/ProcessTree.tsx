import { Background, Controls, MarkerType, ReactFlow, type Edge } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { call, engine, type Schemas } from "../api/client";
import { useLoad } from "../api/useLoad";
import { AddToCase } from "../components/AddToCase";
import { BackButton, EmptyState, ErrorView, Loading, Section, StateBadge, Time } from "../components/common";
import { label } from "../labels";
import { layoutTree, mergeTrace, NODE_HEIGHT } from "../tree/layout";
import { ProcessNodeView, type ProcessFlowNode } from "../tree/ProcessNodeView";

const nodeTypes = { process: ProcessNodeView };
const LEVEL_STYLE: Record<string, { stroke: string; strokeDasharray?: string }> = {
  VERIFIED: { stroke: "#0b6e75" },
  STRONG: { stroke: "#b7791f", strokeDasharray: "6 4" },
  WEAK: { stroke: "#9aa8b5", strokeDasharray: "2 4" },
};
const COMPLETENESS_HELP: Record<Schemas["Completeness"], string> = {
  COMPLETE: "Không thiếu process cha, không bị cắt và không còn event chờ xử lý trong thời gian các process này chạy.",
  MISSING_PARENT: "Thiếu event tạo của process gốc hoặc một process cha: nó nằm ngoài khoảng dữ liệu đã đồng bộ.",
  TRUNCATED: "Cây bị cắt theo giới hạn độ sâu hoặc số process; tăng giới hạn để xem thêm.",
  PENDING_DATA: "Còn event chưa xử lý trên host trong thời gian các process này chạy; cây có thể thay đổi.",
};
const DEPTHS = [3, 5, 10, 20];
const NODE_LIMITS = [100, 500, 1000, 2000, 5000];

type Props = {
  eventId: number;
  initialProcessId?: number;
  onOpenEvent: (id: number) => void;
  onOpenCase: (caseId: number) => void;
};

export function ProcessTree({ eventId, initialProcessId, onOpenEvent, onOpenCase }: Props) {
  const [processId, setProcessId] = useState<number | undefined>(initialProcessId);
  const [includeInferred, setIncludeInferred] = useState(false);
  const [maxDepth, setMaxDepth] = useState(3);
  const [maxNodes, setMaxNodes] = useState(500);
  const [selected, setSelected] = useState<number>();
  const limits = { include_inferred: includeInferred, max_depth: maxDepth, max_nodes: maxNodes };
  const trace = useLoad(async () => {
    const c = await engine();
    return call(c.GET("/trace/event/{event_id}", { params: { path: { event_id: eventId }, query: { process_id: processId, ...limits } } }));
  }, [eventId, processId, includeInferred, maxDepth, maxNodes]);
  const [view, setView] = useState<Schemas["TraceResult"]>();
  const [expandError, setExpandError] = useState<unknown>();
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
      try {
        const c = await engine();
        // Same options as the tree itself, or the expanded part would ignore "inferred" and the limits.
        const extra = await call(c.GET("/trace/expand/{process_id}", { params: { path: { process_id: id }, query: limits } }));
        setView((current) => (current ? mergeTrace(current, extra) : extra));
        setExpandError(undefined);
      } catch (err) {
        setExpandError(err);
      }
    },
    [includeInferred, maxDepth, maxNodes],
  );

  const matchedProcesses = useMemo(() => {
    const matched = new Set(view?.matched_event_ids ?? []);
    return new Set((view?.related_events ?? []).filter((e) => matched.has(e.event_id)).map((e) => e.process_id));
  }, [view]);

  const nodes: ProcessFlowNode[] = (view?.nodes ?? []).map((p) => ({
    id: String(p.id),
    type: "process",
    position: layout.positions.get(p.id) ?? { x: 0, y: p.depth * (NODE_HEIGHT + 40) },
    data: { process: p, matched: matchedProcesses.has(p.id), focus: p.id === view?.focus_process_id, selected: p.id === selected, onExpand: expand },
  }));
  const edges: Edge[] = (view?.edges ?? []).map((e) => ({
    id: `e${e.id}`,
    source: String(e.parent_id),
    target: String(e.child_id),
    label: e.level === "VERIFIED" ? undefined : label("edge_level", e.level),
    style: { ...LEVEL_STYLE[e.level], cursor: e.evidence_event_id ? "pointer" : undefined },
    markerEnd: { type: MarkerType.ArrowClosed },
    data: { evidence: e.evidence_event_id },
  }));
  const selectedNode = view?.nodes.find((n) => n.id === selected);
  const hasCandidates = Boolean(view && view.candidates.length > 0);

  return (
    <Section
      title={`Cây process — event #${eventId}`}
      actions={
        <>
          <BackButton />
          {view && view.nodes.length > 0 && (
            <AddToCase buttonLabel="Lưu ảnh chụp cây vào case" item={() => ({ item_type: "tree_snapshot", snapshot: view })} onOpenCase={onOpenCase} />
          )}
        </>
      }
    >
      <div className="row tree-options">
        <label className="inline">
          <input type="checkbox" checked={includeInferred} onChange={(e) => setIncludeInferred(e.target.checked)} />
          Hiện quan hệ suy luận (mạnh/yếu)
        </label>
        <label className="inline">
          Độ sâu
          <select value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))}>
            {DEPTHS.map((d) => (
              <option key={d} value={d}>
                {d} tầng
              </option>
            ))}
          </select>
        </label>
        <label className="inline">
          Tối đa
          <select value={maxNodes} onChange={(e) => setMaxNodes(Number(e.target.value))}>
            {NODE_LIMITS.map((n) => (
              <option key={n} value={n}>
                {n.toLocaleString("vi-VN")} process
              </option>
            ))}
          </select>
        </label>
      </div>
      <Loading when={trace.loading} />
      <ErrorView error={trace.error ?? expandError} />
      {view && hasCandidates && (
        <div className="notice">
          Event liên kết với nhiều process, chọn một:
          {view.candidates.map((c) => (
            <button key={c.id} onClick={() => setProcessId(c.id)}>
              {c.name} (PID {c.pid})
            </button>
          ))}
        </div>
      )}
      {view && !hasCandidates && (
        <>
          <div className="row">
            <StateBadge kind="completeness" value={view.completeness} />
            <span className="muted">{COMPLETENESS_HELP[view.completeness]}</span>
          </div>
          {view.warnings.length > 0 && (
            <ul className="warnings">
              {view.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
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
                onNodeClick={(_, node) => setSelected(Number(node.id) === selected ? undefined : Number(node.id))}
                onEdgeClick={(_, edge) => {
                  const evidence = (edge.data as { evidence?: number | null } | undefined)?.evidence;
                  if (evidence) onOpenEvent(evidence);
                }}
              >
                <Background />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
          </div>
          <p className="muted">Bấm vào một process để chọn; bấm vào cạnh để mở event làm bằng chứng cho quan hệ đó.</p>
          {selectedNode && (
            <div className="selected-process">
              <div>
                <strong>{selectedNode.name ?? selectedNode.image ?? selectedNode.process_guid}</strong>
                {selectedNode.pid != null && <> · PID {selectedNode.pid}</>}
                {selectedNode.user_name && <> · {selectedNode.user_name}</>}
                {selectedNode.is_placeholder ? (
                  <span className="muted"> · chưa có event tạo process, chỉ biết qua process con</span>
                ) : (
                  <>
                    {" "}
                    · bắt đầu <Time value={selectedNode.started_at} />
                    {selectedNode.ended_at && (
                      <>
                        {" "}
                        · kết thúc <Time value={selectedNode.ended_at} />
                      </>
                    )}
                  </>
                )}
              </div>
              <div className="overflow-cell muted clamp" title={selectedNode.command_line ?? selectedNode.image ?? undefined}>
                {selectedNode.command_line ?? selectedNode.image}
              </div>
              <AddToCase
                buttonLabel="Ghim process này vào case"
                item={() => ({ item_type: "process", ref_id: selectedNode.id })}
                onOpenCase={onOpenCase}
              />
            </div>
          )}
          {!view.nodes.length && <EmptyState title="Không có process">Event này chưa liên kết với process nào.</EmptyState>}
          <h3>Event liên quan ({view.related_events.length.toLocaleString("vi-VN")})</h3>
          {!view.related_events.length ? (
            <EmptyState title="Không có event liên quan">Các process trong cây chưa có event mạng, DNS hay event khác.</EmptyState>
          ) : (
            <div className="table-wrap">
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
                      className={view.matched_event_ids.includes(e.event_id) ? "hl clickable" : "clickable"}
                      onClick={() => onOpenEvent(e.event_id)}
                    >
                      <td>
                        <Time value={e.event_time} />
                      </td>
                      <td className="nowrap">{label("link_type", e.link_type)}</td>
                      <td className="nowrap">{view.nodes.find((n) => n.id === e.process_id)?.name ?? e.process_id}</td>
                      <td className="overflow-cell">
                        <div className="clamp" title={e.summary}>
                          {e.summary}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
