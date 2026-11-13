import { useState } from "react";
import { EngineError, call, engine, type Schemas } from "../api/client";
import { useLoad } from "../api/useLoad";
import { formatBytes } from "../labels";

type Target = "wizard" | "settings" | "sync";
type Notice = { key: string; tone: "bad" | "warn"; text: string; action?: { label: string; target: Target } };

const DAY = 24 * 3600 * 1000;
const EXPIRY_WARNING_DAYS = 7;

/**
 * Problems found by the background sync never reach the UI as request errors (they are source and connection
 * states), so they are polled here and shown on every screen.
 */
export function StatusBanner({ routeKey, onNavigate }: { routeKey: string; onNavigate: (target: Target) => void }) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const state = useLoad(
    async () => {
      const c = await engine();
      const connection = await call(c.GET("/config/elk")).catch((err: unknown) => {
        if (err instanceof EngineError && err.body.code === "NOT_CONFIGURED") return null;
        throw err;
      });
      if (!connection) return { notices: [] };
      const [status, usage] = await Promise.all([call(c.GET("/sync/status")), call(c.GET("/storage/usage"))]);
      return { notices: notices(connection, status, usage) };
    },
    [routeKey],
    { refreshMs: 30000 },
  );
  const visible = (state.data?.notices ?? []).filter((n) => !dismissed.has(n.key));
  if (!visible.length) return null;
  return (
    <div className="banners">
      {visible.map((n) => (
        <div key={n.key} className={`banner ${n.tone}`} role="alert">
          <span>{n.text}</span>
          {n.action && <button onClick={() => onNavigate(n.action!.target)}>{n.action.label}</button>}
          <button onClick={() => setDismissed(new Set(dismissed).add(n.key))}>Ẩn</button>
        </div>
      ))}
    </div>
  );
}

export function notices(connection: Schemas["ElkConnection"], status: Schemas["SyncStatus"], usage: Schemas["StorageUsage"]): Notice[] {
  const found: Notice[] = [];
  const newKey = { label: "Nhập API key mới", target: "wizard" as const };
  const sourceStates = new Set(status.sources.map((s) => s.state));
  if (connection.state === "AUTH_FAILED" || sourceStates.has("AUTH_FAILED")) {
    found.push({ key: "auth", tone: "bad", text: "API key ELK không còn hợp lệ (hết hạn hoặc đã bị thu hồi). Đồng bộ đã dừng.", action: newKey });
  }
  if (connection.state === "FORBIDDEN" || sourceStates.has("FORBIDDEN")) {
    found.push({ key: "forbidden", tone: "bad", text: "API key thiếu quyền đọc index đã cấu hình. Đồng bộ đã dừng.", action: newKey });
  }
  if (connection.state === "TLS_ERROR") {
    found.push({ key: "tls", tone: "bad", text: "Chứng chỉ TLS của ELK không khớp CA đã lưu. Đồng bộ đã dừng.", action: { label: "Cập nhật kết nối", target: "wizard" } });
  }
  if (connection.state === "UNREACHABLE") {
    found.push({ key: "unreachable", tone: "warn", text: "Không kết nối được tới ELK; engine sẽ tự thử lại.", action: { label: "Xem đồng bộ", target: "sync" } });
  }
  if (usage.threshold_state === "BLOCK_90" || sourceStates.has("QUOTA_BLOCKED")) {
    found.push({
      key: "quota",
      tone: "bad",
      text: `Dữ liệu đã chạm 90% hạn mức (${formatBytes(usage.used_bytes)} / ${formatBytes(usage.quota_bytes)}). Đồng bộ tạm dừng cho tới khi tăng hạn mức hoặc giảm thời gian lưu giữ.`,
      action: { label: "Mở cài đặt", target: "settings" },
    });
  } else if (usage.threshold_state === "WARN_80") {
    found.push({ key: "quota80", tone: "warn", text: `Dữ liệu đã dùng hơn 80% hạn mức (${formatBytes(usage.used_bytes)} / ${formatBytes(usage.quota_bytes)}).`, action: { label: "Mở cài đặt", target: "settings" } });
  }
  if (connection.api_key_expires_at && connection.state !== "AUTH_FAILED") {
    const days = Math.ceil((new Date(connection.api_key_expires_at).getTime() - Date.now()) / DAY);
    if (days <= 0) found.push({ key: "expired", tone: "bad", text: "API key ELK đã hết hạn.", action: newKey });
    else if (days <= EXPIRY_WARNING_DAYS) found.push({ key: "expiring", tone: "warn", text: `API key ELK hết hạn sau ${days} ngày.`, action: newKey });
  }
  if (status.scheduler === "blocked" && !found.some((n) => n.tone === "bad")) {
    found.push({ key: "blocked", tone: "warn", text: "Đồng bộ đang bị chặn.", action: { label: "Xem đồng bộ", target: "sync" } });
  }
  return found;
}
