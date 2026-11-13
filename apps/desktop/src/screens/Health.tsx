import { call, engine } from "../api/client";
import { useLoad } from "../api/useLoad";
import { Badge, EmptyState, ErrorView, Loading, Section, Time } from "../components/common";
import { formatDuration } from "../labels";

export function Health() {
  const health = useLoad(async () => call((await engine()).GET("/system/health")), []);
  const h = health.data;
  return (
    <Section title="Trạng thái engine" actions={<button onClick={health.reload}>Làm mới</button>}>
      <Loading when={health.loading} />
      <ErrorView error={health.error} />
      {h && (
        <div className="table-wrap">
          <table>
            <tbody>
              <tr>
                <th>Trạng thái</th>
                <td>
                  <Badge tone={h.status === "ok" ? "ok" : "warn"}>{h.status === "ok" ? "Hoạt động bình thường" : h.status}</Badge>
                </td>
              </tr>
              <tr>
                <th>Engine / contract</th>
                <td>
                  {h.engine_version} / {h.contract_version}
                </td>
              </tr>
              <tr>
                <th>Database</th>
                <td>
                  {h.db.status === "up" ? "Đang chạy" : h.db.status}, schema v{h.db.schema_version ?? "?"}
                </td>
              </tr>
              <tr>
                <th>Uptime</th>
                <td>{formatDuration(h.uptime_s)}</td>
              </tr>
              {Object.entries(h.workers ?? {}).map(([name, w]) => (
                <tr key={name}>
                  <th>Worker {name}</th>
                  <td className="overflow-cell">
                    <Badge tone={w.state === "running" ? "ok" : "warn"}>{w.state === "running" ? "Đang chạy" : w.state}</Badge> lần chạy cuối <Time value={w.last_tick} />
                    {w.last_error && <span className="error-text"> {w.last_error}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!health.loading && !health.error && !h && <EmptyState title="Engine chưa trả trạng thái">Làm mới để kiểm tra trạng thái engine.</EmptyState>}
    </Section>
  );
}
