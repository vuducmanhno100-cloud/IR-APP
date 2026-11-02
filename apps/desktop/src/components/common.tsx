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
    <div className="notice error">
      <strong>{code}</strong> {error.message}
    </div>
  );
}

export function Loading({ when }: { when: boolean }) {
  return when ? <div className="muted">Đang tải…</div> : null;
}

export function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <header>
        <h2>{title}</h2>
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
