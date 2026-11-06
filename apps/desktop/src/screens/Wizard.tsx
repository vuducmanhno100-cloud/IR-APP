import { useEffect, useState, type FormEvent } from "react";
import { call, engine, type Schemas } from "../api/client";
import { Badge, EmptyState, ErrorView, Loading, Section } from "../components/common";

const DEFAULT_LOG = "logs-windows.sysmon_operational-*";
const DEFAULT_ALERT = ".alerts-security.alerts-default";

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

  useEffect(() => setResult(undefined), [url, apiKey, fingerprint, caPem, clusterName, logPattern, alertPattern]);

  const input = (): Schemas["ElkConnectionInput"] => ({
    url,
    api_key: apiKey,
    ca_fingerprint: fingerprint || undefined,
    ca_pem: caPem || undefined,
    cluster_name: clusterName || undefined,
    index_patterns: { log: [logPattern], alert: [alertPattern] },
  });

  async function act(kind: "test" | "save", event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const c = await engine();
      if (kind === "test") {
        setResult(await call(c.POST("/config/elk/test", { body: input() })));
      } else {
        await call(c.PUT("/config/elk", { body: input() }));
        setApiKey("");
        onSaved();
      }
      setError(undefined);
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Kết nối ELK">
      <form className="form" onSubmit={(e) => act("test", e)}>
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
          Index log
          <input value={logPattern} onChange={(e) => setLogPattern(e.target.value)} />
        </label>
        <label>
          Index alert
          <input value={alertPattern} onChange={(e) => setAlertPattern(e.target.value)} />
        </label>
        <div className="row form-actions">
          <button disabled={busy}>Kiểm tra</button>
          <button type="button" disabled={busy || !result?.ok} onClick={(e) => act("save", e)}>
            Lưu và bắt đầu đồng bộ
          </button>
        </div>
      </form>
      <Loading when={busy} />
      <ErrorView error={error} />
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
  );
}
