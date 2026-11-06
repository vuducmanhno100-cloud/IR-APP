import { useEffect, useState, type FormEvent } from "react";
import { call, engine, type Schemas } from "../api/client";
import { useLoad } from "../api/useLoad";
import { Badge, EmptyState, ErrorView, Loading, Section, Time } from "../components/common";

const GB = 1024 ** 3;

export function Settings({ onReconfigure }: { onReconfigure: () => void }) {
  const connection = useLoad(async () => call((await engine()).GET("/config/elk")), []);
  const settings = useLoad(async () => call((await engine()).GET("/config/settings")), []);
  const usage = useLoad(async () => call((await engine()).GET("/storage/usage")), []);
  const [draft, setDraft] = useState<Schemas["Settings"]>();
  const [error, setError] = useState<Error>();

  useEffect(() => setDraft(settings.data), [settings.data]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    try {
      await call((await engine()).PUT("/config/settings", { body: draft }));
      setError(undefined);
      settings.reload();
    } catch (err) {
      setError(err as Error);
    }
  }

  const field = (key: keyof Schemas["Settings"], label: string, scale = 1) =>
    draft && (
      <label>
        {label}
        <input
          type="number"
          value={draft[key] / scale}
          onChange={(e) => setDraft({ ...draft, [key]: Math.round(Number(e.target.value) * scale) })}
        />
      </label>
    );

  const c = connection.data;
  const u = usage.data;
  return (
    <>
      <Section title="Kết nối ELK" actions={<button onClick={onReconfigure}>Đổi API key / kết nối</button>}>
        <ErrorView error={connection.error} />
        <Loading when={connection.loading} />
        {c && (
          <p>
            {c.url} · cluster {c.cluster_name ?? c.cluster_key} ({c.es_version}) · key {c.api_key_name} hết hạn{" "}
            <Time value={c.api_key_expires_at} /> · <Badge tone={c.state === "ACTIVE" ? "ok" : "bad"}>{c.state}</Badge>
          </p>
        )}
        {!connection.loading && !connection.error && !c && <EmptyState title="Chưa có kết nối ELK">Cấu hình Elasticsearch để bắt đầu thu thập dữ liệu.</EmptyState>}
      </Section>
      <Section title="Dung lượng">
        <ErrorView error={usage.error} />
        <Loading when={usage.loading} />
        {u && (
          <p>
            {(u.used_bytes / GB).toFixed(1)} / {(u.quota_bytes / GB).toFixed(0)} GB ({u.percent.toFixed(0)}%) ·{" "}
            <Badge tone={u.threshold_state === "OK" ? "ok" : u.threshold_state === "BLOCK_90" ? "bad" : "warn"}>{u.threshold_state}</Badge>
          </p>
        )}
        {!usage.loading && !usage.error && !u && <EmptyState title="Chưa có dữ liệu dung lượng">Dung lượng lưu trữ chưa được tải.</EmptyState>}
      </Section>
      <Section title="Cài đặt">
        <ErrorView error={settings.error ?? error} />
        <Loading when={settings.loading} />
        {draft && (
          <form className="form" onSubmit={save}>
            {field("sync_interval_s", "Chu kỳ đồng bộ (giây)")}
            {field("quota_bytes", "Hạn mức (GB)", GB)}
            {field("retention_days", "Giữ dữ liệu (ngày)")}
            {field("reconcile_window_h", "Cửa sổ reconcile (giờ)")}
            {field("alert_resync_days", "Re-sync status alert (ngày)")}
            <button>Lưu</button>
          </form>
        )}
        {!settings.loading && !settings.error && !draft && <EmptyState title="Không tải được cài đặt">Thử làm mới dữ liệu hoặc kiểm tra kết nối engine.</EmptyState>}
      </Section>
    </>
  );
}
