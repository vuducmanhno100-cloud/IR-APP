import { useState, type FormEvent } from "react";
import { call, engine, type Schemas } from "../api/client";
import { usePaged } from "../api/useLoad";
import {
  EmptyState,
  ErrorView,
  LoadMore,
  Loading,
  Section,
  StateBadge,
  Time,
  TimeRangeFields,
  defaultRange,
  rangeBounds,
  type TimeRange,
} from "../components/common";
import { label, options } from "../labels";

const PAGE = 100;

export function Alerts({ onOpenEvent }: { onOpenEvent: (id: number) => void }) {
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const [range, setRange] = useState<TimeRange>(defaultRange());
  const bounds = rangeBounds(range);
  const alerts = usePaged(
    async (cursor) =>
      bounds.problem
        ? { items: [] }
        : call(
            (await engine()).GET("/alerts", {
              params: {
                query: {
                  severity: (severity || undefined) as Schemas["AlertSeverity"] | undefined,
                  status: (status || undefined) as Schemas["AlertStatus"] | undefined,
                  from: bounds.from,
                  to: bounds.to,
                  cursor,
                  limit: PAGE,
                },
              },
            }),
          ),
    [severity, status, range.preset, range.from, range.to],
  );
  return (
    <Section title="Alert" actions={<button onClick={alerts.reload}>Làm mới</button>}>
      <div className="filters">
        <label>
          Mức độ
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">Tất cả</option>
            {options("alert_severity").map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Trạng thái
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Tất cả</option>
            {options("alert_status").map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <TimeRangeFields value={range} onChange={setRange} />
      </div>
      {bounds.problem && <div className="notice">{bounds.problem}</div>}
      <Loading when={alerts.loading && !alerts.items.length} />
      <ErrorView error={alerts.error} />
      {!alerts.loading && !alerts.error && !alerts.items.length && <EmptyState title="Không có alert">Không có alert nào khớp bộ lọc.</EmptyState>}
      {alerts.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Thời gian</th>
                  <th>Rule</th>
                  <th>Mức độ</th>
                  <th>Điểm rủi ro</th>
                  <th>Trạng thái</th>
                  <th>Host</th>
                </tr>
              </thead>
              <tbody>
                {alerts.items.map((a) => (
                  <tr
                    key={a.event_id}
                    className="clickable"
                    title={a.original_event_id ? "Mở event gốc gây ra alert" : "Event gốc chưa được đồng bộ; mở bản ghi alert"}
                    onClick={() => onOpenEvent(a.original_event_id ?? a.event_id)}
                  >
                    <td>
                      <Time value={a.alert_time} />
                    </td>
                    <td className="overflow-cell">{a.rule_name ?? a.rule_id ?? a.source_alert_id}</td>
                    <td>
                      <StateBadge kind="alert_severity" value={a.severity} />
                    </td>
                    <td className="nowrap">{a.risk_score ?? "—"}</td>
                    <td>
                      <StateBadge kind="alert_status" value={a.status} />
                    </td>
                    <td className="nowrap">{a.host_name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LoadMore hasMore={alerts.hasMore} loading={alerts.loading} shown={alerts.items.length} onClick={alerts.loadMore} />
        </>
      )}
    </Section>
  );
}

export function Unresolved({ onOpenEvent }: { onOpenEvent: (id: number) => void }) {
  const [status, setStatus] = useState("");
  const items = usePaged(
    async (cursor) =>
      call(
        (await engine()).GET("/trace/unresolved", {
          params: { query: { status: (status || undefined) as "OPEN" | "CONFLICT" | undefined, cursor, limit: PAGE } },
        }),
      ),
    [status],
  );
  return (
    <Section title="Event và quan hệ chưa liên kết" actions={<button onClick={items.reload}>Làm mới</button>}>
      <div className="filters">
        <label>
          Trạng thái
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Chưa giải quyết và xung đột</option>
            <option value="OPEN">{label("unresolved_status", "OPEN")}</option>
            <option value="CONFLICT">{label("unresolved_status", "CONFLICT")}</option>
          </select>
        </label>
      </div>
      <Loading when={items.loading && !items.items.length} />
      <ErrorView error={items.error} />
      {!items.loading && !items.error && !items.items.length && <EmptyState title="Không có quan hệ chưa liên kết">Tất cả quan hệ hiện đã được xử lý.</EmptyState>}
      {items.items.length > 0 && (
        <>
          <div className="table-wrap">
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
                {items.items.map((u) => (
                  <tr key={u.id} className="clickable" onClick={() => onOpenEvent(u.event_id)}>
                    <td className="nowrap">#{u.event_id}</td>
                    <td className="nowrap">{u.host_name}</td>
                    <td className="nowrap">{label("unresolved_relation", u.relation)}</td>
                    <td className="mono">{u.target_guid}</td>
                    <td className="overflow-cell">{u.reason}</td>
                    <td>
                      <StateBadge kind="unresolved_status" value={u.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LoadMore hasMore={items.hasMore} loading={items.loading} shown={items.items.length} onClick={items.loadMore} />
        </>
      )}
    </Section>
  );
}

export function Cases({ onOpenCase }: { onOpenCase: (caseId: number) => void }) {
  const cases = usePaged(async (cursor) => call((await engine()).GET("/cases", { params: { query: { cursor, limit: PAGE } } })), []);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const created = await call((await engine()).POST("/cases", { body: { title: title.trim() } }));
      setTitle("");
      setError(undefined);
      onOpenCase(created.id);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Case & Evidence" actions={<button onClick={cases.reload}>Làm mới</button>}>
      <form className="row" onSubmit={create}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Tên case" required maxLength={200} />
        <button className="button-primary" disabled={busy || !title.trim()}>
          {busy ? "Đang tạo…" : "Tạo case"}
        </button>
      </form>
      <ErrorView error={error ?? cases.error} />
      <Loading when={cases.loading && !cases.items.length} />
      {!cases.loading && !cases.error && !cases.items.length && (
        <EmptyState title="Chưa có case">Tạo case ở đây, hoặc bấm "Thêm vào case" khi xem một event hay cây process.</EmptyState>
      )}
      {cases.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Tên</th>
                  <th>Trạng thái</th>
                  <th>Cập nhật</th>
                </tr>
              </thead>
              <tbody>
                {cases.items.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => onOpenCase(c.id)}>
                    <td className="nowrap">#{c.id}</td>
                    <td className="overflow-cell">{c.title}</td>
                    <td>
                      <StateBadge kind="case_status" value={c.status} />
                    </td>
                    <td>
                      <Time value={c.updated_at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LoadMore hasMore={cases.hasMore} loading={cases.loading} shown={cases.items.length} onClick={cases.loadMore} />
        </>
      )}
    </Section>
  );
}
