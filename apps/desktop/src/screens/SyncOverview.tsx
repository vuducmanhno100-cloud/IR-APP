import { useState } from "react";
import { call, engine } from "../api/client";
import { useLoad } from "../api/useLoad";
import { Badge, EmptyState, ErrorView, Loading, Section, Time } from "../components/common";

const STATE_TONE = { ACTIVE: "ok", PAUSED: "neutral", AUTH_FAILED: "bad", FORBIDDEN: "bad", QUOTA_BLOCKED: "bad", ERROR: "bad" } as const;

export function SyncOverview() {
  const status = useLoad(async () => call((await engine()).GET("/sync/status")), []);
  const [actionError, setActionError] = useState<Error>();

  async function act(path: "/sync/run-now" | "/sync/pause" | "/sync/resume") {
    try {
      await call((await engine()).POST(path, {}));
      setActionError(undefined);
      status.reload();
    } catch (err) {
      setActionError(err as Error);
    }
  }

  return (
    <Section
      title="Tổng quan đồng bộ"
      actions={
        <>
          <button onClick={() => act("/sync/run-now")}>Chạy ngay</button>
          {status.data?.scheduler === "paused" ? (
            <button onClick={() => act("/sync/resume")}>Tiếp tục</button>
          ) : (
            <button onClick={() => act("/sync/pause")}>Tạm dừng</button>
          )}
        </>
      }
    >
      <Loading when={status.loading} />
      <ErrorView error={status.error ?? actionError} />
      {status.data && (
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Trạng thái</th>
              <th>Ingest watermark</th>
              <th>Process watermark</th>
              <th>Độ trễ</th>
              <th>Pending / Failed</th>
              <th>Thành công gần nhất</th>
              <th>Lỗi gần nhất</th>
            </tr>
          </thead>
          <tbody>
            {status.data.sources.map((s) => (
              <tr key={s.source_id}>
                <td>
                  {s.name} <span className="muted">({s.kind})</span>
                </td>
                <td>
                  <Badge tone={STATE_TONE[s.state]}>{s.state}</Badge>
                  {s.active_run && <Badge tone="info">{s.active_run.status}</Badge>}
                </td>
                <td>
                  <Time value={s.ingest_watermark} />
                </td>
                <td>
                  <Time value={s.process_watermark} />
                </td>
                <td>{s.lag_s ?? "—"}s</td>
                <td>
                  {s.pending_events} / {s.failed_events}
                </td>
                <td>
                  <Time value={s.last_success} />
                </td>
                <td>{s.last_error ? `${s.last_error.code}: ${s.last_error.message}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!status.loading && !status.error && !status.data?.sources.length && (
        <EmptyState title="Chưa cấu hình nguồn đồng bộ">Thêm hoặc cấu hình nguồn ELK để bắt đầu đồng bộ.</EmptyState>
      )}
    </Section>
  );
}
