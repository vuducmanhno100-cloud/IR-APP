import { useRef, useState, type FormEvent } from "react";
import { call, engine, type Schemas } from "../api/client";
import { Badge, EmptyState, ErrorView, Loading, Section, Time } from "../components/common";

const TERM_TYPES: Schemas["TermType"][] = ["ip", "domain", "hash", "process_guid", "image", "user", "host", "command_line", "event_code"];
const MATCH_MODES: Schemas["MatchMode"][] = ["exact", "prefix", "suffix", "contains", "cidr"];

type Props = { onOpenEvent: (id: number) => void; onTrace: (id: number) => void };

export function Search({ onOpenEvent, onTrace }: Props) {
  const [type, setType] = useState<Schemas["TermType"]>("domain");
  const [value, setValue] = useState("");
  const [match, setMatch] = useState<Schemas["MatchMode"]>("exact");
  const [result, setResult] = useState<Schemas["SearchResult"]>();
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const [searched, setSearched] = useState(false);
  const requestId = useRef(0);

  async function run(event: FormEvent, cursor?: string) {
    event.preventDefault();
    const currentRequest = ++requestId.current;
    setBusy(true);
    if (!cursor) setResult(undefined);
    try {
      const c = await engine();
      const page = await call(c.GET("/search/events", { params: { query: { type, value, match, cursor, limit: 100 } } }));
      if (currentRequest === requestId.current) {
        setResult((prev) => (cursor && prev ? { ...page, items: [...prev.items, ...page.items] } : page));
        setSearched(true);
        setError(undefined);
      }
    } catch (err) {
      if (currentRequest === requestId.current) setError(err as Error);
    } finally {
      if (currentRequest === requestId.current) setBusy(false);
    }
  }

  return (
    <Section title="Tìm kiếm">
      <form className="search-form" onSubmit={(e) => run(e)}>
        <select value={type} onChange={(e) => setType(e.target.value as Schemas["TermType"])}>
          {TERM_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <select value={match} onChange={(e) => setMatch(e.target.value as Schemas["MatchMode"])}>
          {MATCH_MODES.map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="suspicious.example" required />
        <button className="button-primary" disabled={busy}>{busy ? "Đang tìm…" : "Tìm kiếm"}</button>
      </form>
      <ErrorView error={error} />
      <Loading when={busy} />
      {!searched && !busy && !error && (
        <EmptyState title="Chưa có kết quả">Nhập giá trị và chọn kiểu tìm kiếm để bắt đầu.</EmptyState>
      )}
      {result && (
        <>
          <div className="muted">
            {result.total}
            {result.total_is_approximate ? "+" : ""} kết quả
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
                {result.items.length === 0 && (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState title="Không tìm thấy sự kiện">Thử thay đổi giá trị hoặc chế độ khớp.</EmptyState>
                    </td>
                  </tr>
                )}
                {result.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Time value={item.event_time} />
                    </td>
                    <td className="nowrap">{item.host_name}</td>
                    <td className="nowrap">{item.event_code}</td>
                    <td className="overflow-cell">{item.summary}</td>
                    <td>
                      <Badge tone={item.process_state === "UNLINKED" ? "warn" : "neutral"}>{item.process_state}</Badge>
                    </td>
                    <td className="nowrap">
                      {item.process_id !== null && item.process_id !== undefined && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onTrace(item.id);
                          }}
                        >
                          Mở cây
                        </button>
                      )}
                      <button onClick={() => onOpenEvent(item.id)}>Chi tiết</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.next_cursor && (
            <button onClick={(e) => run(e, result.next_cursor ?? undefined)} disabled={busy}>
              Tải thêm
            </button>
          )}
        </>
      )}
    </Section>
  );
}
