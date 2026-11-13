/** Vietnamese labels and badge tones for contract enums. The code stays visible as a tooltip. */
export type Tone = "neutral" | "ok" | "warn" | "bad" | "info";
type Entry = [label: string, tone: Tone];

const TABLES = {
  process_state: {
    PENDING: ["Chờ xử lý", "info"],
    NORMALIZED: ["Đã chuẩn hóa", "info"],
    CORRELATED: ["Đã liên kết", "ok"],
    UNLINKED: ["Chưa liên kết", "warn"],
    FAILED: ["Lỗi xử lý", "bad"],
    SKIPPED: ["Bỏ qua", "neutral"],
  },
  completeness: {
    COMPLETE: ["Đầy đủ", "ok"],
    MISSING_PARENT: ["Thiếu process cha", "warn"],
    TRUNCATED: ["Bị cắt bớt", "warn"],
    PENDING_DATA: ["Còn dữ liệu chờ xử lý", "info"],
  },
  link_type: {
    create: ["Tạo process", "neutral"],
    terminate: ["Kết thúc", "neutral"],
    network: ["Mạng", "info"],
    dns: ["DNS", "info"],
    other: ["Khác", "neutral"],
  },
  edge_level: {
    VERIFIED: ["Xác minh", "ok"],
    STRONG: ["Suy luận mạnh", "warn"],
    WEAK: ["Suy luận yếu", "bad"],
  },
  source_state: {
    ACTIVE: ["Hoạt động", "ok"],
    PAUSED: ["Tạm dừng", "warn"],
    AUTH_FAILED: ["Lỗi xác thực", "bad"],
    FORBIDDEN: ["Thiếu quyền", "bad"],
    QUOTA_BLOCKED: ["Chạm hạn mức", "bad"],
    ERROR: ["Lỗi", "bad"],
  },
  connection_state: {
    ACTIVE: ["Hoạt động", "ok"],
    AUTH_FAILED: ["Lỗi xác thực", "bad"],
    FORBIDDEN: ["Thiếu quyền", "bad"],
    TLS_ERROR: ["Lỗi TLS", "bad"],
    UNREACHABLE: ["Không kết nối được", "warn"],
  },
  scheduler: {
    running: ["Đang chạy", "ok"],
    paused: ["Tạm dừng", "warn"],
    blocked: ["Bị chặn", "bad"],
  },
  run_status: {
    QUEUED: ["Đang chờ", "neutral"],
    FETCHING: ["Đang tải", "info"],
    RAW_COMMITTED: ["Đã ghi", "info"],
    SUCCEEDED: ["Thành công", "ok"],
    PARTIAL: ["Một phần", "warn"],
    FAILED: ["Thất bại", "bad"],
    CANCELLED: ["Đã hủy", "neutral"],
  },
  run_type: {
    BOOTSTRAP: ["Lấy lịch sử", "info"],
    INCREMENTAL: ["Tăng dần", "neutral"],
    RECONCILE: ["Đối soát", "neutral"],
    ALERT_RESYNC: ["Đồng bộ lại alert", "neutral"],
  },
  case_status: {
    OPEN: ["Mở", "info"],
    IN_PROGRESS: ["Đang xử lý", "warn"],
    CLOSED: ["Đã đóng", "neutral"],
  },
  unresolved_status: {
    OPEN: ["Chưa giải quyết", "warn"],
    CONFLICT: ["Xung đột", "bad"],
    RESOLVED: ["Đã giải quyết", "ok"],
  },
  unresolved_relation: {
    parent: ["Process cha", "neutral"],
    process_link: ["Liên kết event", "neutral"],
  },
  alert_status: {
    open: ["Mở", "warn"],
    acknowledged: ["Đã ghi nhận", "info"],
    closed: ["Đã đóng", "neutral"],
  },
  alert_severity: {
    low: ["Thấp", "neutral"],
    medium: ["Trung bình", "warn"],
    high: ["Cao", "bad"],
    critical: ["Nghiêm trọng", "bad"],
  },
  threshold_state: {
    OK: ["Bình thường", "ok"],
    WARN_70: ["Đã dùng 70%", "warn"],
    WARN_80: ["Đã dùng 80%", "warn"],
    BLOCK_90: ["Chạm 90%, dừng đồng bộ", "bad"],
  },
  case_item_type: {
    event: ["Event", "info"],
    process: ["Process", "info"],
    tree_snapshot: ["Ảnh chụp cây", "info"],
  },
} satisfies Record<string, Record<string, Entry>>;

export type LabelKind = keyof typeof TABLES;

export function label(kind: LabelKind, code: string | null | undefined): string {
  if (!code) return "—";
  return (TABLES[kind] as Record<string, Entry>)[code]?.[0] ?? code;
}

export function tone(kind: LabelKind, code: string | null | undefined): Tone {
  if (!code) return "neutral";
  return (TABLES[kind] as Record<string, Entry>)[code]?.[1] ?? "neutral";
}

export function options(kind: LabelKind): { value: string; label: string }[] {
  return Object.entries(TABLES[kind] as Record<string, Entry>).map(([value, [text]]) => ({ value, label: text }));
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toLocaleString("vi-VN", { maximumFractionDigits: unit >= 3 ? 1 : 0 })} ${units[unit]}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds} giây`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} phút`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} giờ ${minutes % 60} phút`;
  return `${Math.floor(hours / 24)} ngày ${hours % 24} giờ`;
}
