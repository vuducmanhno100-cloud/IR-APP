import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { Schemas } from "../api/client";

export type ProcessNodeData = {
  process: Schemas["ProcessNode"];
  matched: boolean;
  focus: boolean;
  selected: boolean;
  onExpand: (processId: number) => void;
};

export type ProcessFlowNode = Node<ProcessNodeData, "process">;

export function ProcessNodeView({ data }: NodeProps<ProcessFlowNode>) {
  const p = data.process;
  const classes = ["proc", data.focus ? "focus" : "", data.matched ? "matched" : "", data.selected ? "selected" : "", p.is_placeholder ? "placeholder" : ""];
  const events = p.event_counts.network + p.event_counts.dns + p.event_counts.other;
  return (
    <div className={classes.join(" ")} title={p.command_line ?? ""}>
      <Handle type="target" position={Position.Top} />
      <div className="proc-name">
        {p.name ?? p.image ?? p.process_guid}
        {p.is_placeholder && <span className="tag" title="Chỉ biết qua event của process con; không có event tạo process">chưa có event tạo</span>}
      </div>
      <div className="proc-meta">
        {[p.pid != null ? `PID ${p.pid}` : "PID ?", p.user_name].filter(Boolean).join(" · ")}
        {events > 0 && <span className="tag info">{events} event</span>}
      </div>
      {p.collapsed_children > 0 && (
        <button
          className="expand"
          title={`${p.collapsed_children} process con đang ẩn`}
          onClick={(e) => {
            e.stopPropagation();
            data.onExpand(p.id);
          }}
        >
          +{p.collapsed_children}
        </button>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
