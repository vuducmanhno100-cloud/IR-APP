import { call, engine } from "../api/client";
import { useLoad } from "../api/useLoad";
import { AddToCase } from "../components/AddToCase";
import { BackButton, EmptyState, ErrorView, Loading, Section, StateBadge, Time } from "../components/common";

type Props = { eventId: number; onTrace: (eventId: number, processId?: number) => void; onOpenCase: (caseId: number) => void };

export function EventDetail({ eventId, onTrace, onOpenCase }: Props) {
  const ev = useLoad(async () => call((await engine()).GET("/events/{event_id}", { params: { path: { event_id: eventId } } })), [eventId]);
  const e = ev.data;
  return (
    <Section
      title={`Event #${eventId}`}
      actions={
        <>
          <BackButton />
          {e?.process_id != null && <button onClick={() => onTrace(eventId)}>Mở cây</button>}
          {e && <AddToCase buttonLabel="Thêm vào case" item={() => ({ item_type: "event", ref_id: eventId })} onOpenCase={onOpenCase} />}
        </>
      }
    >
      <Loading when={ev.loading} />
      <ErrorView error={ev.error} />
      {e && (
        <>
          <p>
            <Time value={e.event_time} /> · {e.host_name} · EID {e.event_code} · <StateBadge kind="process_state" value={e.process_state} />
          </p>
          <p className="overflow-cell">{e.summary}</p>
          <p className="muted">
            Nguồn: {e.source.cluster_key} / {e.source.index_name} / {e.source.elastic_id} · Elasticsearch nhận lúc <Time value={e.ingest_time} />
          </p>
          <h3>Process liên kết</h3>
          {e.linked_processes.length === 0 ? (
            <p className="muted">Event chưa liên kết với process nào.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Process</th>
                    <th>Liên kết</th>
                    <th>Mức tin cậy</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {e.linked_processes.map((p) => (
                    <tr key={`${p.process_id}-${p.link_type}`}>
                      <td className="nowrap">#{p.process_id}</td>
                      <td>
                        <StateBadge kind="link_type" value={p.link_type} />
                      </td>
                      <td>
                        <StateBadge kind="edge_level" value={p.level} />
                      </td>
                      <td className="nowrap">
                        <button onClick={() => onTrace(eventId, p.process_id)}>Mở cây của process này</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <h3>Trường chuẩn hóa</h3>
          <div className="table-wrap">
            <table>
              <tbody>
                {Object.entries(e.normalized).map(([k, v]) => (
                  <tr key={k}>
                    <th>{k}</th>
                    <td className="overflow-cell">{v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Raw payload</h3>
          <pre className="json">{JSON.stringify(e.payload, null, 2)}</pre>
        </>
      )}
      {!ev.loading && !ev.error && !e && <EmptyState title="Không tìm thấy event">Event này không tồn tại hoặc không còn trong dữ liệu lưu trữ.</EmptyState>}
    </Section>
  );
}
