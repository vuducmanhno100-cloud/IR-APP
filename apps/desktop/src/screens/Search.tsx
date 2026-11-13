import { useEffect, useRef, useState, type FormEvent } from "react";
import { call, engine, type Schemas } from "../api/client";
import type { paths } from "../api/schema";
import {
  EmptyState,
  ErrorView,
  LoadMore,
  Loading,
  Section,
  StateBadge,
  Time,
  TimeRangeFields,
  defaultRange,
  rangeBounds,
  type TimeRange,
} from "../components/common";

type TermType = Schemas["TermType"];
type MatchMode = Schemas["MatchMode"];
type SearchQuery = paths["/search/events"]["get"]["parameters"]["query"];

/** Match modes the engine accepts per term type (investigation/service.py search). */
const TERMS: Record<TermType, { label: string; placeholder: string; modes: MatchMode[] }> = {
  domain: { label: "Tên miền", placeholder: "cdn-update.example.net", modes: ["exact", "suffix", "prefix"] },
  ip: { label: "Địa chỉ IP", placeholder: "203.0.113.66 hoặc 203.0.113.0/24", modes: ["exact", "cidr", "prefix"] },
  command_line: { label: "Command line", placeholder: "-encodedcommand", modes: ["contains", "prefix", "exact"] },
  image: { label: "Chương trình (image)", placeholder: "powershell.exe hoặc C:\\Windows\\System32\\", modes: ["exact", "prefix"] },
  user: { label: "Người dùng", placeholder: "analyst hoặc LAB\\analyst", modes: ["exact", "prefix"] },
  hash: { label: "Hash (MD5/SHA1/SHA256)", placeholder: "3f2a…", modes: ["exact", "prefix"] },
  host: { label: "Tên host", placeholder: "ws-03", modes: ["exact", "prefix"] },
  process_guid: { label: "Process GUID", placeholder: "{4f1c…}", modes: ["exact", "prefix"] },
  event_code: { label: "Mã event Sysmon", placeholder: "1, 3, 22…", modes: ["exact"] },
};

const MODES: Record<MatchMode, string> = {
  exact: "Khớp chính xác",
  prefix: "Bắt đầu bằng",
  suffix: "Là domain hoặc domain con của",
  contains: "Chứa chuỗi",
  cidr: "Nằm trong dải mạng (CIDR)",
};

type Form = { type: TermType; match: MatchMode; value: string; range: TimeRange; host: string; eventCode: string };
type Memory = { form: Form; result?: Schemas["SearchResult"]; query?: SearchQuery; scrollY: number };

// Kept for the whole session so opening an event or a tree and coming back shows the same results.
let memory: Memory = { form: { type: "domain", match: "exact", value: "", range: defaultRange(), host: "", eventCode: "" }, scrollY: 0 };

type Props = { onOpenEvent: (id: number) => void; onTrace: (id: number) => void };

export function Search({ onOpenEvent, onTrace }: Props) {
  const [form, setForm] = useState<Form>(memory.form);
  const [result, setResult] = useState<Schemas["SearchResult"] | undefined>(memory.result);
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  const query = useRef(memory.query);

  useEffect(() => {
    // <main> is the scrolling element (styles.css), not the window.
    const scroller = document.querySelector("main");
    if (!scroller) return;
    scroller.scrollTop = memory.scrollY;
    const remember = () => (memory.scrollY = scroller.scrollTop);
    scroller.addEventListener("scroll", remember);
    return () => scroller.removeEventListener("scroll", remember);
  }, [Boolean(result)]);
  useEffect(() => {
    memory = { ...memory, form, result, query: query.current };
  }, [form, result]);

  const needsRange = form.match === "contains";
  const bounds = rangeBounds(form.range);
  const problem = bounds.problem ?? (needsRange && !bounds.from ? "Tìm \"chứa chuỗi\" cần chọn khoảng thời gian." : undefined);

  const update = (patch: Partial<Form>) => setForm((current) => ({ ...current, ...patch }));
  const changeType = (type: TermType) => {
    // The first mode is the useful default for the type (e.g. "contains" for command lines).
    const match = TERMS[type].modes[0];
    update({ type, match, range: match === "contains" && form.range.preset === "all" ? { ...form.range, preset: "24h" } : form.range });
  };
  const changeMatch = (match: MatchMode) =>
    update({ match, range: match === "contains" && form.range.preset === "all" ? { ...form.range, preset: "24h" } : form.range });

  async function run(cursor?: string) {
    const currentRequest = ++requestId.current;
    setBusy(true);
    if (!cursor) {
      setResult(undefined);
      query.current = {
        type: form.type,
        value: form.value,
        match: form.match,
        from: bounds.from,
        to: bounds.to,
        host: form.host.trim() || undefined,
        event_code: form.eventCode.trim() || undefined,
        limit: 100,
      };
    }
    try {
      const c = await engine();
      const page = await call(c.GET("/search/events", { params: { query: { ...query.current!, cursor } } }));
      if (currentRequest === requestId.current) {
        setResult((prev) => (cursor && prev ? { ...page, items: [...prev.items, ...page.items] } : page));
        setError(undefined);
      }
    } catch (err) {
      if (currentRequest === requestId.current) setError(err);
    } finally {
      if (currentRequest === requestId.current) setBusy(false);
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!problem) run();
  };
  const term = TERMS[form.type];

  return (
    <Section title="Tìm kiếm">
      <form className="search-form" onSubmit={submit}>
        <div className="search-main">
          <label>
            Tìm theo
            <select value={form.type} onChange={(e) => changeType(e.target.value as TermType)}>
              {Object.entries(TERMS).map(([value, t]) => (
                <option key={value} value={value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Cách khớp
            <select value={form.match} onChange={(e) => changeMatch(e.target.value as MatchMode)}>
              {term.modes.map((m) => (
                <option key={m} value={m}>
                  {MODES[m]}
                </option>
              ))}
            </select>
          </label>
          <label className="grow">
            Giá trị
            <input value={form.value} onChange={(e) => update({ value: e.target.value })} placeholder={term.placeholder} required />
          </label>
          <button className="button-primary" disabled={busy || Boolean(problem)}>
            {busy ? "Đang tìm…" : "Tìm kiếm"}
          </button>
        </div>
        <div className="search-filters">
          <TimeRangeFields value={form.range} onChange={(range) => update({ range })} allowAll={!needsRange} />
          <label>
            Lọc theo host
            <input value={form.host} onChange={(e) => update({ host: e.target.value })} placeholder="mọi host" />
          </label>
          <label>
            Lọc theo mã event
            <input value={form.eventCode} onChange={(e) => update({ eventCode: e.target.value })} placeholder="mọi mã" inputMode="numeric" />
          </label>
        </div>
      </form>
      {problem && <div className="notice">{problem}</div>}
      <ErrorView error={error} />
      <Loading when={busy && !result} />
      {!result && !busy && !error && <EmptyState title="Chưa có kết quả">Chọn loại giá trị, nhập giá trị cần tìm và bấm Tìm kiếm.</EmptyState>}
      {result && result.items.length === 0 && <EmptyState title="Không tìm thấy event">Thử cách khớp khác, khoảng thời gian rộng hơn hoặc bỏ bộ lọc.</EmptyState>}
      {result && result.items.length > 0 && (
        <>
          <div className="muted">
            {result.total.toLocaleString("vi-VN")}
            {result.total_is_approximate ? "+" : ""} event khớp
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Thời gian</th>
                  <th>Host</th>
                  <th>Event</th>
                  <th>Nội dung</th>
                  <th>Trạng thái</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {result.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Time value={item.event_time} />
                    </td>
                    <td className="nowrap">{item.host_name}</td>
                    <td className="nowrap">{item.event_code}</td>
                    <td className="overflow-cell">
                      <div className="clamp" title={item.summary}>
                        {item.summary}
                      </div>
                    </td>
                    <td>
                      <StateBadge kind="process_state" value={item.process_state} />
                    </td>
                    <td className="nowrap">
                      {item.process_id != null && <button onClick={() => onTrace(item.id)}>Mở cây</button>}
                      <button onClick={() => onOpenEvent(item.id)}>Chi tiết</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LoadMore hasMore={Boolean(result.next_cursor)} loading={busy} shown={result.items.length} onClick={() => run(result.next_cursor ?? undefined)} />
        </>
      )}
    </Section>
  );
}
