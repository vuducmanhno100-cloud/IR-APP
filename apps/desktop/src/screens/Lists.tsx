import { useState, type FormEvent } from "react";
import { call, engine } from "../api/client";
import { useLoad } from "../api/useLoad";
import { Badge, ErrorView, Loading, Section, Time } from "../components/common";

const SEVERITY_TONE = { low: "neutral", medium: "info", high: "warn", critical: "bad" } as const;

export function Alerts({ onOpenEvent }: { onOpenEvent: (id: number) => void }) {
  const alerts = useLoad(async () => call((await engine()).GET("/alerts", { params: { query: { limit: 200 } } })), []);
  return (
    <Section title="Alert" actions={<button onClick={alerts.reload}>Làm mới</button>}>
      <Loading when={alerts.loading} />
      <ErrorView error={alerts.error} />
      <table>
        <thead>
          <tr>
            <th>Thời gian</th>
            <th>Rule</th>
            <th>Mức độ</th>
            <th>Trạng thái</th>
            <th>Host</th>
          </tr>
        </thead>
        <tbody>
          {alerts.data?.items.map((a) => (
            <tr key={a.event_id} onClick={() => onOpenEvent(a.original_event_id ?? a.event_id)}>
              <td>
                <Time value={a.alert_time} />
              </td>
              <td>{a.rule_name}</td>
              <td>{a.severity && <Badge tone={SEVERITY_TONE[a.severity]}>{a.severity}</Badge>}</td>
              <td>{a.status}</td>
              <td>{a.host_name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

export function Unresolved({ onOpenEvent }: { onOpenEvent: (id: number) => void }) {
  const items = useLoad(async () => call((await engine()).GET("/trace/unresolved", { params: { query: { limit: 200 } } })), []);
  return (
    <Section title="Event và quan hệ chưa liên kết">
      <Loading when={items.loading} />
      <ErrorView error={items.error} />
      <table>
        <thead>
          <tr>
            <th>Event</th>
            <th>Host</th>
            <th>Quan hệ</th>
            <th>GUID đích</th>
            <th>Lý do</th>
            <th>Trạng thái</th>
          </tr>
        </thead>
        <tbody>
          {items.data?.items.map((u) => (
            <tr key={u.id} onClick={() => onOpenEvent(u.event_id)}>
              <td>#{u.event_id}</td>
              <td>{u.host_name}</td>
              <td>{u.relation}</td>
              <td className="mono">{u.target_guid}</td>
              <td>{u.reason}</td>
              <td>
                <Badge tone={u.status === "CONFLICT" ? "bad" : "warn"}>{u.status}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

export function Cases() {
  const cases = useLoad(async () => call((await engine()).GET("/cases", { params: { query: { limit: 200 } } })), []);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<Error>();

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      await call((await engine()).POST("/cases", { body: { title } }));
      setTitle("");
      setError(undefined);
      cases.reload();
    } catch (err) {
      setError(err as Error);
    }
  }

  return (
    <Section title="Case & Evidence">
      <form className="row" onSubmit={create}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Tên case" required />
        <button>Tạo case</button>
      </form>
      <Loading when={cases.loading} />
      <ErrorView error={cases.error ?? error} />
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Tên</th>
            <th>Trạng thái</th>
            <th>Cập nhật</th>
          </tr>
        </thead>
        <tbody>
          {cases.data?.items.map((c) => (
            <tr key={c.id}>
              <td>{c.id}</td>
              <td>{c.title}</td>
              <td>{c.status}</td>
              <td>
                <Time value={c.updated_at} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}
