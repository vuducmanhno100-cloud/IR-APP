import { useEffect, useState, type FormEvent } from "react";
import { call, engine, type Schemas } from "../api/client";
import { useLoad } from "../api/useLoad";
import { Badge, EmptyState, ErrorView, Loading, Section } from "../components/common";

const DEFAULT_LOG = "logs-windows.sysmon_operational-*";
const DEFAULT_ALERT = ".alerts-security.alerts-default";
const HOUR = 3600 * 1000;
const GB = 1024 ** 3;
// Measured in the lab simulation: raw event plus search terms and process tree rows.
const BYTES_PER_EVENT = 6.5 * 1024;
const RANGES = [
  { value: "24h", label: "24 giờ trước (mặc định)", hours: 24 },
  { value: "3d", label: "3 ngày trước", hours: 72 },
  { value: "7d", label: "7 ngày trước", hours: 168 },
  { value: "30d", label: "30 ngày trước", hours: 720 },
] as const;
type Range = (typeof RANGES)[number]["value"] | "custom" | "none";

// Sources the wizard creates (elk/probe.py suggest_sources); their patterns follow the fields below.
const LOG_SOURCE = "sysmon";
const ALERT_SOURCE = "security-alerts";

function localInput(date: Date): string {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

function splitPatterns(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((p) => p.trim())
    .filter(Boolean);
}

export function Wizard({ onSaved }: { onSaved: () => void }) {
  const [url, setUrl] = useState("https://");
  const [apiKey, setApiKey] = useState("");
  const [fingerprint, setFingerprint] = useState("");
  const [caPem, setCaPem] = useState("");
  const [clusterName, setClusterName] = useState("");
  const [logPattern, setLogPattern] = useState(DEFAULT_LOG);
  const [alertPattern, setAlertPattern] = useState(DEFAULT_ALERT);
  const [result, setResult] = useState<Schemas["ElkTestResult"]>();
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);

  const existing = useLoad(async () => call((await engine()).GET("/config/elk")), []);
  const settings = useLoad(async () => call((await engine()).GET("/config/settings")), []);
  const sources = useLoad(async () => (existing.data ? call((await engine()).GET("/config/sources")) : []), [existing.data]);
  const firstConnection = !existing.loading && !existing.data;
  const [range, setRange] = useState<Range>("24h");
  const [customFrom, setCustomFrom] = useState(() => localInput(new Date(Date.now() - 24 * HOUR)));
  const [quotaGb, setQuotaGb] = useState<number>();
  const [estimate, setEstimate] = useState<Schemas["ElkTestResult"]["counts"]>();
  const [estimating, setEstimating] = useState(false);

  useEffect(() => setResult(undefined), [url, apiKey, fingerprint, caPem, clusterName, logPattern, alertPattern]);
  useEffect(() => {
    if (!existing.data) return;
    // Show what is saved; only the API key has to be typed again (it never leaves Credential Manager).
    setRange("none");
    setUrl(existing.data.url);
    setFingerprint(existing.data.ca_fingerprint ?? "");
    setClusterName(existing.data.cluster_name ?? "");
  }, [existing.data]);
  useEffect(() => {
    const byName = new Map((sources.data ?? []).map((s) => [s.name, s.index_patterns.join(", ")]));
    if (byName.has(LOG_SOURCE)) setLogPattern(byName.get(LOG_SOURCE)!);
    if (byName.has(ALERT_SOURCE)) setAlertPattern(byName.get(ALERT_SOURCE)!);
  }, [sources.data]);
  useEffect(() => {
    if (settings.data) setQuotaGb(Math.round(settings.data.quota_bytes / GB));
  }, [settings.data]);

  const input = (): Schemas["ElkConnectionInput"] => ({
    url,
    api_key: apiKey,
    ca_fingerprint: fingerprint || undefined,
    ca_pem: caPem || undefined,
    cluster_name: clusterName || undefined,
    index_patterns: { log: splitPatterns(logPattern), alert: splitPatterns(alertPattern) },
  });

  const retentionDays = settings.data?.retention_days ?? 30;
  const historyFrom = (now: number): Date | undefined => {
    if (range === "none") return undefined;
    const chosen = range === "custom" ? new Date(customFrom) : new Date(now - RANGES.find((r) => r.value === range)!.hours * HOUR);
    // The first connection always covers at least the 24 hours before the moment the user saves.
    return firstConnection && chosen.getTime() > now - 24 * HOUR ? new Date(now - 24 * HOUR) : chosen;
  };
  const rangeProblem = (() => {
    const now = Date.now();
    const from = historyFrom(now);
    if (!from) return undefined;
    if (Number.isNaN(from.getTime())) return "Chọn thời điểm bắt đầu.";
    if (from.getTime() >= now) return "Thời điểm bắt đầu phải ở quá khứ.";
    if (from.getTime() < now - retentionDays * 24 * HOUR) return `Cũ hơn thời gian lưu giữ (${retentionDays} ngày); dữ liệu sẽ bị xóa ngay.`;
    return undefined;
  })();

  useEffect(() => {
    setEstimate(undefined);
    const from = historyFrom(Date.now());
    if (!result?.ok || !from || rangeProblem) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      setEstimating(true);
      try {
        const c = await engine();
        const counted = await call(c.POST("/config/elk/test", { params: { query: { history_from: from.toISOString() } }, body: input() }));
        if (active) setEstimate(counted.counts);
      } catch {
        if (active) setEstimate(undefined);
      } finally {
        if (active) setEstimating(false);
      }
    }, 400);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [result, range, customFrom, firstConnection, retentionDays]);

  async function test(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      setResult(await call((await engine()).POST("/config/elk/test", { body: input() })));
      setError(undefined);
    } catch (err) {
      setResult(undefined);
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (rangeProblem) return;
    setBusy(true);
    try {
      const c = await engine();
      const quota = Math.round((quotaGb ?? 30) * GB);
      if (settings.data && quota !== settings.data.quota_bytes) {
        await call(c.PUT("/config/settings", { body: { ...settings.data, quota_bytes: quota } }));
      }
      await call(c.PUT("/config/elk", { body: input() }));
      const now = Date.now();
      const from = historyFrom(now);
      if (from) {
        await call(c.POST("/sync/bootstrap", { body: { from: from.toISOString(), to: new Date(now).toISOString() } }));
      }
      setApiKey("");
      setError(undefined);
      onSaved();
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  const alertPatterns = new Set(splitPatterns(alertPattern));
  const logCount = estimate?.filter((c) => !alertPatterns.has(c.pattern)).reduce((sum, c) => sum + (c.history_count ?? 0), 0);
  const alertCount = estimate?.filter((c) => alertPatterns.has(c.pattern)).reduce((sum, c) => sum + (c.history_count ?? 0), 0) ?? 0;
  const estimatedGb = logCount !== undefined ? ((logCount + alertCount) * BYTES_PER_EVENT) / GB : undefined;
  const overQuota = estimatedGb !== undefined && quotaGb !== undefined && estimatedGb > quotaGb * 0.9;

  return (
    <>
      <Section title="Kết nối ELK">
        <form className="form" onSubmit={test}>
          <label>
            Elasticsearch URL
            <input value={url} onChange={(e) => setUrl(e.target.value)} pattern="https://.*" required />
          </label>
          <label>
            API key (encoded)
            <input type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} required />
          </label>
          <label>
            CA SHA-256 fingerprint
            <input value={fingerprint} onChange={(e) => setFingerprint(e.target.value)} placeholder="AA:BB:..." />
          </label>
          <label>
            hoặc CA certificate (PEM)
            <textarea rows={3} value={caPem} onChange={(e) => setCaPem(e.target.value)} />
          </label>
          <label>
            Tên cluster (khi key không có quyền monitor)
            <input value={clusterName} onChange={(e) => setClusterName(e.target.value)} />
          </label>
          <label>
            Index log (nhiều pattern cách nhau bằng dấu phẩy)
            <input value={logPattern} onChange={(e) => setLogPattern(e.target.value)} />
          </label>
          <label>
            Index alert (nhiều pattern cách nhau bằng dấu phẩy)
            <input value={alertPattern} onChange={(e) => setAlertPattern(e.target.value)} />
          </label>
          {existing.data && (
            <p className="muted">
              Đang có kết nối: nhập lại API key để kiểm tra. Pattern ở đây áp dụng cho source "{LOG_SOURCE}" và "{ALERT_SOURCE}"; source khác chỉnh ở Đồng bộ › Cấu hình source.
            </p>
          )}
          <div className="row form-actions">
            <button disabled={busy}>Kiểm tra</button>
          </div>
        </form>
        <Loading when={busy && !result} />
        {!result && <ErrorView error={error} />}
        {result && (
          <div className="grid2">
            <div>
              <h3>
                Kết quả <Badge tone={result.ok ? "ok" : "bad"}>{result.ok ? "Đạt" : "Chưa đạt"}</Badge>
              </h3>
              <p>
                Key: {result.authenticated?.api_key_name} · Cluster: {result.cluster?.name} ({result.cluster?.version})
              </p>
              {result.warnings.map((w) => (
                <div key={w} className="notice">
                  {w}
                </div>
              ))}
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Pattern</th>
                    <th>read</th>
                    <th>view_index_metadata</th>
                    <th>Số document</th>
                  </tr>
                </thead>
                <tbody>
                  {result.privileges.map((p) => (
                    <tr key={p.pattern}>
                      <td>{p.pattern}</td>
                      <td>{p.read ? "✓" : "✗"}</td>
                      <td>{p.view_index_metadata ? "✓" : "✗"}</td>
                      <td>{result.counts.find((c) => c.pattern === p.pattern)?.count ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {!result && !busy && !error && (
          <EmptyState title="Sẵn sàng kiểm tra kết nối">Nhập kết nối ELK, sau đó kiểm tra quyền truy cập trước khi lưu.</EmptyState>
        )}
      </Section>

      {result?.ok && (
        <Section title="Lấy lịch sử và dung lượng">
          <form className="form" onSubmit={save}>
            <label>
              Lấy log từ
              <select value={range} onChange={(e) => setRange(e.target.value as Range)}>
                {existing.data && <option value="none">Không lấy thêm (giữ dữ liệu đã có)</option>}
                {RANGES.map((r) => (
                  <option key={r.value} value={r.value} disabled={r.hours > retentionDays * 24}>
                    {r.label}
                    {r.hours > retentionDays * 24 ? ` (quá ${retentionDays} ngày lưu giữ)` : ""}
                  </option>
                ))}
                <option value="custom">Chọn thời điểm…</option>
              </select>
            </label>
            {range === "custom" && (
              <label>
                Từ thời điểm
                <input type="datetime-local" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} required />
              </label>
            )}
            <label>
              Hạn mức dung lượng local (GB)
              <input
                type="number"
                min={1}
                max={100}
                step={1}
                value={quotaGb ?? ""}
                onChange={(e) => setQuotaGb(Number(e.target.value))}
                required
              />
            </label>
            <div className="row form-actions">
              <button className="button-primary" disabled={busy || Boolean(rangeProblem) || settings.loading}>
                {busy ? "Đang lưu…" : "Lưu và bắt đầu đồng bộ"}
              </button>
            </div>
          </form>
          {rangeProblem && <div className="notice error">{rangeProblem}</div>}
          {!rangeProblem && range !== "none" && (
            <p>
              {estimating && <span className="muted">Đang ước lượng…</span>}
              {!estimating && logCount !== undefined && (
                <>
                  Ước lượng: <strong>{logCount.toLocaleString("vi-VN")}</strong> event log ·{" "}
                  <strong>{alertCount.toLocaleString("vi-VN")}</strong> alert · {estimatedGb! < 0.1 ? "dưới 0,1 GB" : `khoảng ${estimatedGb!.toLocaleString("vi-VN", { maximumFractionDigits: 1 })} GB`}{" "}
                  <span className="muted">(≈ 6,5 KB/event gồm dữ liệu tìm kiếm và cây)</span>
                </>
              )}
            </p>
          )}
          {overQuota && (
            <div className="notice">Ước lượng vượt 90% hạn mức; đồng bộ sẽ tạm dừng khi chạm 90%. Tăng hạn mức hoặc lấy khoảng ngắn hơn.</div>
          )}
          {range === "none" && <p className="muted">Giữ nguyên dữ liệu đã đồng bộ; chỉ cập nhật kết nối.</p>}
          {firstConnection && <p className="muted">Lần đầu kết nối, ứng dụng luôn lấy ít nhất 24 giờ trước thời điểm bấm lưu.</p>}
          <ErrorView error={error} />
        </Section>
      )}
    </>
  );
}
