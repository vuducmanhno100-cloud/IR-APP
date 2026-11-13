import { useEffect, useState } from "react";
import { call, canRestartEngine, engine, type Schemas } from "../api/client";
import { useLoad } from "../api/useLoad";
import { BackButton, EmptyState, ErrorView, Loading, Section, StateBadge, Time } from "../components/common";
import { label, options } from "../labels";

type Props = { caseId: number; onOpenEvent: (id: number) => void; onOpenTree: (eventId: number, processId?: number) => void };

async function openReport(path: string): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("open_export", { path });
}

export function CaseDetail({ caseId, onOpenEvent, onOpenTree }: Props) {
  const detail = useLoad(async () => call((await engine()).GET("/cases/{case_id}", { params: { path: { case_id: caseId } } })), [caseId]);
  const data = detail.data;
  const [status, setStatus] = useState<Schemas["CaseStatus"]>("OPEN");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) {
      setStatus(data.status);
      setNotes(data.notes ?? "");
    }
  }, [data]);

  async function run(what: string, action: () => Promise<unknown>) {
    setBusy(what);
    setSaved(false);
    try {
      await action();
      setError(undefined);
      detail.reload();
      return true;
    } catch (err) {
      setError(err);
      return false;
    } finally {
      setBusy(undefined);
    }
  }

  const save = () =>
    run("save", async () =>
      call((await engine()).PATCH("/cases/{case_id}", { params: { path: { case_id: caseId } }, body: { status, notes: notes.trim() || null } })),
    ).then((ok) => setSaved(Boolean(ok)));
  const remove = (itemId: number) =>
    run(`remove-${itemId}`, async () =>
      call((await engine()).DELETE("/cases/{case_id}/items/{item_id}", { params: { path: { case_id: caseId, item_id: itemId } } })),
    );
  const exportReport = () =>
    run("export", async () => {
      const file = await call((await engine()).POST("/cases/{case_id}/export", { params: { path: { case_id: caseId } }, body: { format: "html" } }));
      if (canRestartEngine) await openReport(file.artifact_path);
    });
  const changed = data && (status !== data.status || (notes.trim() || null) !== (data.notes ?? null));

  return (
    <Section
      title={data ? `Case #${caseId}: ${data.title}` : `Case #${caseId}`}
      actions={
        <>
          <BackButton />
          <button className="button-primary" disabled={!data || busy === "export"} onClick={exportReport}>
            {busy === "export" ? "Đang xuất…" : "Xuất báo cáo HTML"}
          </button>
        </>
      }
    >
      <Loading when={detail.loading && !data} />
      <ErrorView error={error ?? detail.error} />
      {data && (
        <>
          <div className="form case-form">
            <label>
              Trạng thái
              <select value={status} onChange={(e) => setStatus(e.target.value as Schemas["CaseStatus"])}>
                {options("case_status").map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Ghi chú
              <textarea rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Diễn biến, kết luận, việc cần làm…" />
            </label>
            <div className="row form-actions">
              <button className="button-primary" disabled={!changed || busy === "save"} onClick={save}>
                {busy === "save" ? "Đang lưu…" : "Lưu"}
              </button>
              {saved && <span className="muted">Đã lưu.</span>}
              <span className="muted">
                Tạo <Time value={data.created_at} /> · cập nhật <Time value={data.updated_at} />
              </span>
            </div>
          </div>

          <h3>Bằng chứng ({data.items.length})</h3>
          {data.items.length === 0 ? (
            <EmptyState title="Chưa có bằng chứng">Mở một event hoặc cây process và bấm "Thêm vào case" để ghim vào đây.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Loại</th>
                    <th>Tham chiếu</th>
                    <th>Ghi chú</th>
                    <th>Ghim lúc</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <StateBadge kind="case_item_type" value={item.item_type} />
                      </td>
                      <td className="overflow-cell">
                        <ItemReference item={item} onOpenEvent={onOpenEvent} onOpenTree={onOpenTree} />
                      </td>
                      <td className="overflow-cell">{item.note ?? <span className="muted">—</span>}</td>
                      <td>
                        <Time value={item.pinned_at} />
                      </td>
                      <td className="nowrap">
                        <button disabled={busy === `remove-${item.id}`} onClick={() => remove(item.id)}>
                          Gỡ khỏi case
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3>Báo cáo đã xuất ({data.files.length})</h3>
          {data.files.length === 0 ? (
            <p className="muted">Chưa xuất báo cáo nào.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Thời gian</th>
                    <th>File</th>
                    <th>SHA-256</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {[...data.files].reverse().map((f) => (
                    <tr key={f.id}>
                      <td>
                        <Time value={f.created_at} />
                      </td>
                      <td className="mono">{f.artifact_path}</td>
                      <td className="mono">{f.sha256}</td>
                      <td className="nowrap">
                        {canRestartEngine ? (
                          <button onClick={() => run(`open-${f.id}`, () => openReport(f.artifact_path))}>Mở</button>
                        ) : (
                          <button onClick={() => navigator.clipboard?.writeText(f.artifact_path)}>Chép đường dẫn</button>
                        )}
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

function ItemReference({ item, onOpenEvent, onOpenTree }: { item: Schemas["CaseItem"]; onOpenEvent: Props["onOpenEvent"]; onOpenTree: Props["onOpenTree"] }) {
  if (item.item_type === "event" && item.ref_id != null) {
    return (
      <button className="link" onClick={() => onOpenEvent(item.ref_id!)}>
        Event #{item.ref_id}
      </button>
    );
  }
  if (item.item_type === "process") return <span>Process #{item.ref_id}</span>;
  const snapshot = item.snapshot;
  if (!snapshot) return <span className="muted">Ảnh chụp trống</span>;
  const root = snapshot.nodes.find((n) => n.id === snapshot.root_id) ?? snapshot.nodes[0];
  const matched = snapshot.matched_event_ids[0];
  return (
    <span>
      {snapshot.nodes.length} process, gốc {root?.name ?? "?"} ({label("completeness", snapshot.completeness)})
      {matched != null && (
        <>
          {" "}
          ·{" "}
          <button className="link" onClick={() => onOpenTree(matched, snapshot.focus_process_id ?? undefined)}>
            mở cây hiện tại
          </button>
        </>
      )}
    </span>
  );
}
