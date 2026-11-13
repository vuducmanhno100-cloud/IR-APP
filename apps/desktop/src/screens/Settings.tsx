import { useEffect, useState, type FormEvent } from "react";
import { EngineError, call, engine, type Schemas } from "../api/client";
import { useLoad } from "../api/useLoad";
import { EmptyState, ErrorView, Loading, Section, StateBadge, Time } from "../components/common";
import { formatBytes } from "../labels";

const GB = 1024 ** 3;
type Key = keyof Schemas["Settings"];
/** Same bounds as Settings in contracts/openapi.yaml; quota is entered in GB. */
const FIELDS: { key: Key; label: string; min: number; max?: number; scale?: number; hint: string }[] = [
  { key: "sync_interval_s", label: "Chu kỳ đồng bộ (giây)", min: 60, max: 3600, hint: "60–3600 giây" },
  { key: "quota_bytes", label: "Hạn mức dung lượng (GB)", min: 1, max: 100, scale: GB, hint: "1–100 GB; đồng bộ dừng khi dùng tới 90%" },
  { key: "retention_days", label: "Giữ dữ liệu (ngày)", min: 1, hint: "event cũ hơn bị xóa, trừ event đã ghim vào case" },
  { key: "reconcile_window_h", label: "Cửa sổ đối soát (giờ)", min: 1, max: 168, hint: "1–168 giờ" },
  { key: "alert_resync_days", label: "Đồng bộ lại trạng thái alert (ngày)", min: 1, max: 90, hint: "1–90 ngày" },
];

type Draft = Record<Key, string>;

export function Settings({ onReconfigure, onDisconnected }: { onReconfigure: () => void; onDisconnected: () => void }) {
  const connection = useLoad(
    async () =>
      call((await engine()).GET("/config/elk")).catch((err: unknown) => {
        if (err instanceof EngineError && err.body.code === "NOT_CONFIGURED") return null;
        throw err;
      }),
    [],
  );
  const settings = useLoad(async () => call((await engine()).GET("/config/settings")), []);
  const usage = useLoad(async () => call((await engine()).GET("/storage/usage")), []);
  const [draft, setDraft] = useState<Draft>();
  const [confirmRetention, setConfirmRetention] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [error, setError] = useState<unknown>();
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data) {
      const data = settings.data;
      setDraft(Object.fromEntries(FIELDS.map((f) => [f.key, String(data[f.key] / (f.scale ?? 1))])) as Draft);
      setConfirmRetention(false);
    }
  }, [settings.data]);

  const problems = draft
    ? FIELDS.flatMap((f) => {
        const value = Number(draft[f.key]);
        if (draft[f.key].trim() === "" || !Number.isInteger(value)) return [`${f.label}: nhập số nguyên.`];
        if (value < f.min || (f.max !== undefined && value > f.max)) return [`${f.label}: ${f.hint.split(";")[0]}.`];
        return [];
      })
    : [];
  const newRetention = Number(draft?.retention_days);
  const shrinking = Boolean(settings.data && newRetention < settings.data.retention_days);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || !settings.data || problems.length || (shrinking && !confirmRetention)) return;
    const body = { ...settings.data };
    for (const f of FIELDS) body[f.key] = Math.round(Number(draft[f.key]) * (f.scale ?? 1));
    try {
      await call((await engine()).PUT("/config/settings", { body }));
      setError(undefined);
      setSaved(true);
      settings.reload();
      usage.reload();
    } catch (err) {
      setError(err);
    }
  }

  async function disconnect() {
    try {
      await call((await engine()).DELETE("/config/elk"));
      setError(undefined);
      onDisconnected();
    } catch (err) {
      setError(err);
    }
  }

  const c = connection.data;
  const u = usage.data;
  const daysLeft = u?.growth_bytes_per_day ? Math.max(0, (u.quota_bytes * 0.9 - u.used_bytes) / u.growth_bytes_per_day) : undefined;
  return (
    <>
      <Section
        title="Kết nối ELK"
        actions={
          c ? (
            <>
              <button onClick={onReconfigure}>Đổi API key / kết nối</button>
              {!confirmDisconnect ? (
                <button onClick={() => setConfirmDisconnect(true)}>Ngắt kết nối</button>
              ) : (
                <>
                  <button className="button-danger" onClick={disconnect}>
                    Xác nhận ngắt kết nối
                  </button>
                  <button onClick={() => setConfirmDisconnect(false)}>Hủy</button>
                </>
              )}
            </>
          ) : (
            <button onClick={onReconfigure}>Kết nối ELK</button>
          )
        }
      >
        <ErrorView error={connection.error} />
        <Loading when={connection.loading} />
        {confirmDisconnect && (
          <div className="notice">
            Ngắt kết nối sẽ xóa API key khỏi Credential Manager và dừng đồng bộ. Dữ liệu đã đồng bộ và case vẫn được giữ.
          </div>
        )}
        {c && (
          <div className="table-wrap">
            <table>
              <tbody>
                <tr>
                  <th>Elasticsearch</th>
                  <td className="overflow-cell">{c.url}</td>
                </tr>
                <tr>
                  <th>Cluster</th>
                  <td>
                    {c.cluster_name ?? c.cluster_key} {c.es_version && <span className="muted">(phiên bản {c.es_version})</span>}
                  </td>
                </tr>
                <tr>
                  <th>API key</th>
                  <td>
                    {c.api_key_name ?? c.api_key_id ?? "—"} · hết hạn <Time value={c.api_key_expires_at} />
                  </td>
                </tr>
                <tr>
                  <th>Trạng thái</th>
                  <td>
                    <StateBadge kind="connection_state" value={c.state} />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
        {!connection.loading && !connection.error && !c && <EmptyState title="Chưa có kết nối ELK">Cấu hình Elasticsearch để bắt đầu thu thập dữ liệu.</EmptyState>}
      </Section>

      <Section title="Dung lượng" actions={<button onClick={usage.reload}>Làm mới</button>}>
        <ErrorView error={usage.error} />
        <Loading when={usage.loading && !u} />
        {u && (
          <>
            <p>
              {formatBytes(u.used_bytes)} / {formatBytes(u.quota_bytes)} ({u.percent.toFixed(0)}%) · <StateBadge kind="threshold_state" value={u.threshold_state} />
            </p>
            <div className="meter" role="img" aria-label={`Đã dùng ${u.percent.toFixed(0)}% hạn mức`}>
              <span style={{ width: `${Math.min(100, u.percent)}%` }} className={u.threshold_state === "OK" ? "" : u.threshold_state === "BLOCK_90" ? "bad" : "warn"} />
            </div>
            <div className="table-wrap">
              <table>
                <tbody>
                  <tr>
                    <th>Cơ sở dữ liệu</th>
                    <td>{formatBytes(u.breakdown.database)}</td>
                  </tr>
                  <tr>
                    <th>WAL</th>
                    <td>{formatBytes(u.breakdown.wal)}</td>
                  </tr>
                  <tr>
                    <th>Báo cáo đã xuất</th>
                    <td>{formatBytes(u.breakdown.exports)}</td>
                  </tr>
                  <tr>
                    <th>File tạm</th>
                    <td>{formatBytes(u.breakdown.temp)}</td>
                  </tr>
                  <tr>
                    <th>Tăng mỗi ngày</th>
                    <td>
                      {u.growth_bytes_per_day == null ? (
                        <span className="muted">chưa đủ dữ liệu để ước lượng</span>
                      ) : (
                        <>
                          {formatBytes(u.growth_bytes_per_day)}
                          {daysLeft !== undefined && <> · còn khoảng {Math.floor(daysLeft).toLocaleString("vi-VN")} ngày tới ngưỡng 90%</>}
                        </>
                      )}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>

      <Section title="Cài đặt">
        <ErrorView error={settings.error ?? error} />
        <Loading when={settings.loading && !draft} />
        {draft && (
          <form className="form" onSubmit={save} noValidate>
            {FIELDS.map((f) => (
              <label key={f.key}>
                {f.label}
                <input
                  type="number"
                  min={f.min}
                  max={f.max}
                  step={1}
                  value={draft[f.key]}
                  onChange={(e) => {
                    setSaved(false);
                    setDraft({ ...draft, [f.key]: e.target.value });
                  }}
                />
                <span className="field-hint">{f.hint}</span>
              </label>
            ))}
            {problems.map((p) => (
              <div key={p} className="notice error">
                {p}
              </div>
            ))}
            {shrinking && (
              <label className="inline notice">
                <input type="checkbox" checked={confirmRetention} onChange={(e) => setConfirmRetention(e.target.checked)} />
                Tôi hiểu event cũ hơn {newRetention} ngày (trừ event đã ghim vào case) sẽ bị xóa ở lần dọn dữ liệu tới.
              </label>
            )}
            <div className="row form-actions">
              <button className="button-primary" disabled={problems.length > 0 || (shrinking && !confirmRetention)}>
                Lưu
              </button>
              {saved && <span className="muted">Đã lưu.</span>}
            </div>
          </form>
        )}
      </Section>
    </>
  );
}
