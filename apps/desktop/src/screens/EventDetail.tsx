import { call, engine } from "../api/client";
import { useLoad } from "../api/useLoad";
import { Badge, ErrorView, Loading, Section, Time } from "../components/common";

export function EventDetail({ eventId, onTrace }: { eventId: number; onTrace: (id: number) => void }) {
  const ev = useLoad(async () => call((await engine()).GET("/events/{event_id}", { params: { path: { event_id: eventId } } })), [eventId]);
  const e = ev.data;
  return (
    <Section title={`Event #${eventId}`} actions={e?.process_id ? <button onClick={() => onTrace(eventId)}>Mở cây</button> : null}>
      <Loading when={ev.loading} />
      <ErrorView error={ev.error} />
      {e && (
        <>
          <p>
            <Time value={e.event_time} /> · {e.host_name} · EID {e.event_code} · <Badge>{e.process_state}</Badge>
          </p>
          <p>{e.summary}</p>
          <p className="muted">
            Nguồn: {e.source.cluster_key} / {e.source.index_name} / {e.source.elastic_id}
          </p>
          <h3>Trường chuẩn hóa</h3>
          <table>
            <tbody>
              {Object.entries(e.normalized).map(([k, v]) => (
                <tr key={k}>
                  <th>{k}</th>
                  <td>{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Raw payload</h3>
          <pre className="json">{JSON.stringify(e.payload, null, 2)}</pre>
        </>
      )}
    </Section>
  );
}
