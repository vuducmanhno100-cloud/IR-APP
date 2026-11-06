import type { ReactNode } from "react";
import { EngineError } from "../api/client";

export function ErrorView({ error }: { error: Error | undefined }) {
  if (!error) return null;
  if (error instanceof EngineError && error.body.code === "NOT_IMPLEMENTED") {
    const owner = String(error.body.detail?.owner ?? "");
    return <div className="notice">Chức năng chưa có trong engine ({owner}).</div>;
  }
  const code = error instanceof EngineError ? error.body.code : "ERROR";
  return (
    <div className="notice error" role="alert">
      <strong>{code}</strong> {error.message}
    </div>
  );
}

export function Loading({ when }: { when: boolean }) {
  return when ? (
    <div className="loading-state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>Đang tải dữ liệu…</span>
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

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "warn" | "bad" | "info"; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Time({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return <time dateTime={value}>{new Date(value).toLocaleString("vi-VN", { hour12: false })}</time>;
}
