import { useEffect, useState } from "react";
import { call, engine, type Schemas } from "../api/client";
import { useLoad, usePaged } from "../api/useLoad";
import { Badge, EmptyState, ErrorView, LoadMore, Loading, Section, StateBadge, Time } from "../components/common";
import { formatDuration } from "../labels";

const REFRESH_MS = 5000;
const RETRYABLE: Schemas["RunStatus"][] = ["FAILED", "PARTIAL"];

export function SyncOverview() {
  const status = useLoad(async () => call((await engine()).GET("/sync/status")), [], { refreshMs: REFRESH_MS });
  const [actionError, setActionError] = useState<unknown>();
  const [notice, setNotice] = useState<string>();
  const runs = usePaged(async (cursor) => call((await engine()).GET("/sync/runs", { params: { query: { cursor, limit: 20 } } })), []);
  const names = new Map((status.data?.sources ?? []).map((s) => [s.source_id, s.name]));

  // The first page of runs follows the status refresh; pages loaded with "Tải thêm" stay put.
  useEffect(() => {
    if (runs.items.length <= 20 && !runs.loading) runs.reload();
  }, [status.data]);

  async function act(path: "/sync/run-now" | "/sync/pause" | "/sync/resume", done: string) {
    try {
      await call((await engine()).POST(path, {}));
      setActionError(undefined);
      setNotice(done);
      status.reload();
    } catch (err) {
      setActionError(err);
      setNotice(undefined);
    }
  }

  async function retry(run: Schemas["SyncRun"]) {
    try {
      const created = await call((await engine()).POST("/sync/retry/{run_id}", { params: { path: { run_id: run.id } } }));
      setActionError(undefined);
      setNotice(`Đã tạo run #${created.id} chạy lại cửa sổ của run #${run.id}.`);
      status.reload();
    } catch (err) {
      setActionError(err);
    }
  }

  const scheduler = status.data?.scheduler;
  return (
    <>
      <Section
        title="Tổng quan đồng bộ"
        actions={
          <>
            {scheduler && <StateBadge kind="scheduler" value={scheduler} />}
            <button onClick={status.reload}>Làm mới</button>
            <button onClick={() => act("/sync/run-now", "Đã yêu cầu đồng bộ ngay.")}>Chạy ngay</button>
            {scheduler === "paused" ? (
              <button onClick={() => act("/sync/resume", "Đã tiếp tục đồng bộ.")}>Tiếp tục</button>
            ) : (
              <button onClick={() => act("/sync/pause", "Đã tạm dừng đồng bộ.")}>Tạm dừng</button>
            )}
          </>
        }
      >
        <p className="muted">Tự cập nhật mỗi {REFRESH_MS / 1000} giây.</p>
        {notice && <div className="notice">{notice}</div>}
        <Loading when={status.loading && !status.data} />
        <ErrorView error={actionError ?? status.error} />
        {status.data && status.data.sources.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Trạng thái</th>
                  <th>Đã lấy về tới</th>
                  <th>Đã xử lý tới</th>
                  <th>Độ trễ</th>
                  <th>Chờ / lỗi xử lý</th>
                  <th>Thành công gần nhất</th>
                  <th>Lỗi gần nhất</th>
                </tr>
              </thead>
              <tbody>
                {status.data.sources.map((s) => (
                  <tr key={s.source_id}>
                    <td className="nowrap">
                      {s.name} <span className="muted">({s.kind})</span>
                    </td>
                    <td>
                      <StateBadge kind="source_state" value={s.state} />{" "}
                      {s.active_run && <StateBadge kind="run_status" value={s.active_run.status} />}
                    </td>
                    <td>
                      <Time value={s.ingest_watermark} />
                    </td>
                    <td>
                      <Time value={s.process_watermark} />
                    </td>
                    <td className="nowrap">{formatDuration(s.lag_s)}</td>
                    <td className="nowrap">
                      {s.pending_events.toLocaleString("vi-VN")} / {s.failed_events.toLocaleString("vi-VN")}
                    </td>
                    <td>
                      <Time value={s.last_success} />
                    </td>
                    <td className="overflow-cell">
                      {s.last_error ? (
                        <>
                          <Badge tone="bad">{s.last_error.code}</Badge> {s.last_error.message} (<Time value={s.last_error.at} />)
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!status.loading && !status.error && !status.data?.sources.length && (
          <EmptyState title="Chưa cấu hình nguồn đồng bộ">Kết nối ELK ở mục "Kết nối ELK" để bắt đầu đồng bộ.</EmptyState>
        )}
      </Section>

      <Section title="Lịch sử chạy" actions={<button onClick={runs.reload}>Làm mới</button>}>
        <ErrorView error={runs.error} />
        <Loading when={runs.loading && !runs.items.length} />
        {!runs.loading && !runs.error && !runs.items.length && <EmptyState title="Chưa có lần chạy nào" />}
        {runs.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Source</th>
                    <th>Loại</th>
                    <th>Trạng thái</th>
                    <th>Cửa sổ dữ liệu</th>
                    <th>Lấy / ghi / trùng</th>
                    <th>Kết thúc</th>
                    <th>Lỗi</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {runs.items.map((r) => (
                    <tr key={r.id}>
                      <td className="nowrap">#{r.id}</td>
                      <td className="nowrap">{names.get(r.source_id) ?? r.source_id}</td>
                      <td className="nowrap">
                        <StateBadge kind="run_type" value={r.run_type} />
                      </td>
                      <td>
                        <StateBadge kind="run_status" value={r.status} />
                      </td>
                      <td>
                        <Time value={r.lower_bound} /> → <Time value={r.upper_bound} />
                      </td>
                      <td className="nowrap">
                        {r.fetched_count.toLocaleString("vi-VN")} / {r.upserted_count.toLocaleString("vi-VN")} /{" "}
                        {r.duplicate_skipped.toLocaleString("vi-VN")}
                      </td>
                      <td>
                        <Time value={r.finished_at} />
                      </td>
                      <td className="overflow-cell">{r.error ? `${r.error_code ?? ""} ${r.error}` : "—"}</td>
                      <td className="nowrap">{RETRYABLE.includes(r.status) && <button onClick={() => retry(r)}>Chạy lại</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <LoadMore hasMore={runs.hasMore} loading={runs.loading} shown={runs.items.length} onClick={runs.loadMore} />
          </>
        )}
      </Section>

      <SourcesEditor onSaved={status.reload} />
    </>
  );
}

type SourceDraft = Schemas["Source"] & { patternsText: string };

function SourcesEditor({ onSaved }: { onSaved: () => void }) {
  const sources = useLoad(async () => call((await engine()).GET("/config/sources")), []);
  const [drafts, setDrafts] = useState<SourceDraft[]>([]);
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => setDrafts((sources.data ?? []).map((s) => ({ ...s, patternsText: s.index_patterns.join("\n") }))), [sources.data]);

  const patch = (id: number, change: Partial<SourceDraft>) => {
    setSaved(false);
    setDrafts((current) => current.map((d) => (d.id === id ? { ...d, ...change } : d)));
  };
  const patterns = (d: SourceDraft) =>
    d.patternsText
      .split(/[\n,]/)
      .map((p) => p.trim())
      .filter(Boolean);
  const problems = drafts.flatMap((d) => [
    ...(patterns(d).length ? [] : [`${d.name}: cần ít nhất một index pattern.`]),
    ...(d.batch_size >= 100 && d.batch_size <= 10000 ? [] : [`${d.name}: batch size từ 100 đến 10.000.`]),
    ...(d.overlap_s >= 0 && d.safety_lag_s >= 0 ? [] : [`${d.name}: overlap và safety lag không được âm.`]),
  ]);

  async function save() {
    setBusy(true);
    try {
      const body: Schemas["SourceInput"][] = drafts.map((d) => ({
        name: d.name,
        kind: d.kind,
        index_patterns: patterns(d),
        timestamp_strategy: d.timestamp_strategy,
        timestamp_field: d.timestamp_field,
        overlap_s: d.overlap_s,
        safety_lag_s: d.safety_lag_s,
        batch_size: d.batch_size,
        enabled: d.enabled,
      }));
      await call((await engine()).PUT("/config/sources", { body }));
      setError(undefined);
      setSaved(true);
      sources.reload();
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (!sources.loading && !sources.error && !drafts.length) return null;
  return (
    <Section
      title="Cấu hình source"
      actions={
        <button className="button-primary" disabled={busy || problems.length > 0 || !drafts.length} onClick={save}>
          {busy ? "Đang lưu…" : "Lưu cấu hình source"}
        </button>
      }
    >
      <p className="muted">
        Index mới thêm vào pattern được đọc từ thời điểm đã đồng bộ tới trở đi; muốn lấy dữ liệu cũ hơn của chúng, dùng bước "Lấy log từ" trong Kết nối ELK.
      </p>
      <Loading when={sources.loading && !drafts.length} />
      <ErrorView error={error ?? sources.error} />
      {problems.map((p) => (
        <div key={p} className="notice">
          {p}
        </div>
      ))}
      {saved && <div className="notice">Đã lưu cấu hình source.</div>}
      {drafts.length > 0 && (
        <div className="table-wrap">
          <table className="sources-table">
            <thead>
              <tr>
                <th>Bật</th>
                <th>Source</th>
                <th>Index pattern (mỗi dòng một pattern)</th>
                <th>Batch</th>
                <th>Overlap (giây)</th>
                <th>Safety lag (giây)</th>
                <th>Mốc thời gian</th>
              </tr>
            </thead>
            <tbody>
              {drafts.map((d) => (
                <tr key={d.id}>
                  <td>
                    <input type="checkbox" checked={d.enabled} onChange={(e) => patch(d.id, { enabled: e.target.checked })} aria-label={`Bật ${d.name}`} />
                  </td>
                  <td className="nowrap">
                    {d.name} <span className="muted">({d.kind})</span>
                  </td>
                  <td>
                    <textarea wrap="off" rows={Math.max(2, patterns(d).length + 1)} value={d.patternsText} onChange={(e) => patch(d.id, { patternsText: e.target.value })} />
                  </td>
                  <td>
                    <input type="number" min={100} max={10000} step={100} value={d.batch_size} onChange={(e) => patch(d.id, { batch_size: Number(e.target.value) })} />
                  </td>
                  <td>
                    <input type="number" min={0} value={d.overlap_s} onChange={(e) => patch(d.id, { overlap_s: Number(e.target.value) })} />
                  </td>
                  <td>
                    <input type="number" min={0} value={d.safety_lag_s} onChange={(e) => patch(d.id, { safety_lag_s: Number(e.target.value) })} />
                  </td>
                  <td className="nowrap muted">
                    {d.timestamp_strategy} · {d.timestamp_field}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
