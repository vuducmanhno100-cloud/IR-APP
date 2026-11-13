import { useState, type ReactNode } from "react";
import { EngineDownError, EngineError, canRestartEngine, restartEngine } from "../api/client";
import { label, tone, type LabelKind } from "../labels";

export function ErrorView({ error }: { error: unknown }) {
  if (!error) return null;
  if (error instanceof EngineError && error.body.code === "NOT_IMPLEMENTED") {
    const owner = String(error.body.detail?.owner ?? "");
    return <div className="notice">Chức năng chưa có trong engine ({owner}).</div>;
  }
  if (error instanceof EngineDownError) return <EngineDown error={error} />;
  const code = error instanceof EngineError ? error.body.code : "LỖI";
  const text = error instanceof Error ? error.message : String(error);
  return (
    <div className="notice error" role="alert">
      <strong>{code}</strong> {text}
    </div>
  );
}

function EngineDown({ error }: { error: Error }) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  async function restart() {
    setBusy(true);
    try {
      await restartEngine();
      window.location.reload();
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }
  return (
    <div className="notice error" role="alert">
      <strong>Engine không hoạt động.</strong> {failure ?? error.message}
      <div className="row notice-actions">
        {canRestartEngine ? (
          <button type="button" className="button-primary" disabled={busy} onClick={restart}>
            {busy ? "Đang khởi động lại…" : "Khởi động lại engine"}
          </button>
        ) : (
          <button type="button" onClick={() => window.location.reload()}>
            Thử lại
          </button>
        )}
      </div>
    </div>
  );
}

export function Loading({ when, text = "Đang tải dữ liệu…" }: { when: boolean; text?: string }) {
  return when ? (
    <div className="loading-state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{text}</span>
    </div>
  ) : null;
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {children && <span>{children}</span>}
    </div>
  );
}

export function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <header>
        <h1>{title}</h1>
        <div className="actions">{actions}</div>
      </header>
      {children}
    </section>
  );
}

export function Badge({ tone = "neutral", title, children }: { tone?: "neutral" | "ok" | "warn" | "bad" | "info"; title?: string; children: ReactNode }) {
  return (
    <span className={`badge ${tone}`} title={title}>
      {children}
    </span>
  );
}

/** Badge for a contract enum value: Vietnamese label, the raw code as tooltip. */
export function StateBadge({ kind, value }: { kind: LabelKind; value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return (
    <Badge tone={tone(kind, value)} title={value}>
      {label(kind, value)}
    </Badge>
  );
}

export function Time({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return <time dateTime={value}>{new Date(value).toLocaleString("vi-VN", { hour12: false })}</time>;
}

export function BackButton() {
  return (
    <button type="button" onClick={() => window.history.back()}>
      ← Quay lại
    </button>
  );
}

export function LoadMore({ hasMore, loading, onClick, shown }: { hasMore: boolean; loading: boolean; onClick: () => void; shown: number }) {
  if (!hasMore) return shown ? <p className="muted list-footer">Đã hiện tất cả {shown.toLocaleString("vi-VN")} dòng.</p> : null;
  return (
    <div className="row list-footer">
      <span className="muted">Đang hiện {shown.toLocaleString("vi-VN")} dòng.</span>
      <button type="button" disabled={loading} onClick={onClick}>
        {loading ? "Đang tải…" : "Tải thêm"}
      </button>
    </div>
  );
}

export const RANGE_PRESETS = [
  { value: "all", label: "Mọi thời điểm", hours: 0 },
  { value: "1h", label: "1 giờ qua", hours: 1 },
  { value: "24h", label: "24 giờ qua", hours: 24 },
  { value: "7d", label: "7 ngày qua", hours: 168 },
  { value: "30d", label: "30 ngày qua", hours: 720 },
  { value: "custom", label: "Tùy chọn…", hours: 0 },
] as const;

export type TimeRange = { preset: (typeof RANGE_PRESETS)[number]["value"]; from: string; to: string };

export function localInput(date: Date): string {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

export function defaultRange(preset: TimeRange["preset"] = "all"): TimeRange {
  const now = new Date();
  return { preset, from: localInput(new Date(now.getTime() - 24 * 3600 * 1000)), to: localInput(now) };
}

/** ISO bounds for the API; undefined means no bound. */
export function rangeBounds(range: TimeRange): { from?: string; to?: string; problem?: string } {
  if (range.preset === "all") return {};
  if (range.preset === "custom") {
    const from = new Date(range.from);
    const to = new Date(range.to);
    if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) return { problem: "Chọn đủ thời điểm bắt đầu và kết thúc." };
    if (from >= to) return { problem: "Thời điểm bắt đầu phải trước thời điểm kết thúc." };
    return { from: from.toISOString(), to: to.toISOString() };
  }
  const hours = RANGE_PRESETS.find((p) => p.value === range.preset)!.hours;
  const now = Date.now();
  return { from: new Date(now - hours * 3600 * 1000).toISOString(), to: new Date(now).toISOString() };
}

export function TimeRangeFields({ value, onChange, allowAll = true }: { value: TimeRange; onChange: (next: TimeRange) => void; allowAll?: boolean }) {
  return (
    <>
      <label>
        Thời gian
        <select value={value.preset} onChange={(e) => onChange({ ...value, preset: e.target.value as TimeRange["preset"] })}>
          {RANGE_PRESETS.filter((p) => allowAll || p.value !== "all").map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      {value.preset === "custom" && (
        <>
          <label>
            Từ
            <input type="datetime-local" value={value.from} onChange={(e) => onChange({ ...value, from: e.target.value })} />
          </label>
          <label>
            Đến
            <input type="datetime-local" value={value.to} onChange={(e) => onChange({ ...value, to: e.target.value })} />
          </label>
        </>
      )}
    </>
  );
}
