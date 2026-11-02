import { call, engine } from "../api/client";
import { useLoad } from "../api/useLoad";
import { Badge, ErrorView, Loading, Section, Time } from "../components/common";

export function Health() {
  const health = useLoad(async () => call((await engine()).GET("/system/health")), []);
  const h = health.data;
  return (
    <Section title="Trạng thái engine" actions={<button onClick={health.reload}>Làm mới</button>}>
      <Loading when={health.loading} />
      <ErrorView error={health.error} />
      {h && (
        <table>
          <tbody>
            <tr>
              <th>Trạng thái</th>
              <td>
                <Badge tone={h.status === "ok" ? "ok" : "warn"}>{h.status}</Badge>
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
                {h.db.status}, schema v{h.db.schema_version ?? "?"}
              </td>
            </tr>
            <tr>
              <th>Uptime</th>
              <td>{h.uptime_s}s</td>
            </tr>
            {Object.entries(h.workers ?? {}).map(([name, w]) => (
              <tr key={name}>
                <th>Worker {name}</th>
                <td>
                  <Badge tone={w.state === "running" ? "ok" : "warn"}>{w.state}</Badge> lần chạy cuối <Time value={w.last_tick} />
                  {w.last_error && <span className="error-text"> {w.last_error}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}
