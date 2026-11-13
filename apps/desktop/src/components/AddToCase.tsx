import { useState, type FormEvent } from "react";
import { call, engine, type Schemas } from "../api/client";
import { useLoad } from "../api/useLoad";
import { label } from "../labels";
import { ErrorView } from "./common";

type ItemInput = Schemas["CaseItemInput"];

/** Pins an event, a process or a tree snapshot to an existing case or to a new one. */
export function AddToCase({ buttonLabel, item, onOpenCase }: { buttonLabel: string; item: () => ItemInput; onOpenCase: (caseId: number) => void }) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("new");
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [added, setAdded] = useState<{ id: number; title: string }>();
  const cases = useLoad(async () => (open ? call((await engine()).GET("/cases", { params: { query: { limit: 100 } } })) : undefined), [open]);
  const openCases = (cases.data?.items ?? []).filter((c) => c.status !== "CLOSED");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const c = await engine();
      const chosen =
        target === "new"
          ? await call(c.POST("/cases", { body: { title: title.trim() } }))
          : openCases.find((x) => String(x.id) === target)!;
      await call(
        c.POST("/cases/{case_id}/items", { params: { path: { case_id: chosen.id } }, body: { ...item(), note: note.trim() || undefined } }),
      );
      setAdded({ id: chosen.id, title: chosen.title });
      setError(undefined);
      setOpen(false);
      setNote("");
      setTitle("");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="add-to-case">
      <button type="button" aria-expanded={open} onClick={() => (setOpen(!open), setAdded(undefined))}>
        {buttonLabel}
      </button>
      {added && (
        <span className="muted">
          Đã thêm vào{" "}
          <button type="button" className="link" onClick={() => onOpenCase(added.id)}>
            {added.title}
          </button>
        </span>
      )}
      {open && (
        <AddToCaseForm
          target={target}
          setTarget={setTarget}
          openCases={openCases}
          title={title}
          setTitle={setTitle}
          note={note}
          setNote={setNote}
          busy={busy}
          error={error ?? cases.error}
          onSubmit={submit}
          onCancel={() => setOpen(false)}
        />
      )}
    </span>
  );
}

type FormProps = {
  target: string;
  setTarget: (v: string) => void;
  openCases: Schemas["Case"][];
  title: string;
  setTitle: (v: string) => void;
  note: string;
  setNote: (v: string) => void;
  busy: boolean;
  error: unknown;
  onSubmit: (event: FormEvent) => void;
  onCancel: () => void;
};

function AddToCaseForm({ target, setTarget, openCases, title, setTitle, note, setNote, busy, error, onSubmit, onCancel }: FormProps) {
  return (
    <form className="form add-to-case-form" onSubmit={onSubmit} onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <label>
        Case
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="new">Tạo case mới…</option>
          {openCases.map((c) => (
            <option key={c.id} value={c.id}>
              #{c.id} {c.title} ({label("case_status", c.status)})
            </option>
          ))}
        </select>
      </label>
      {target === "new" && (
        <label>
          Tên case mới
          <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
        </label>
      )}
      <label>
        Ghi chú (không bắt buộc)
        <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
      </label>
      <div className="row form-actions">
        <button className="button-primary" disabled={busy || (target === "new" && !title.trim())}>
          {busy ? "Đang thêm…" : "Thêm"}
        </button>
        <button type="button" onClick={onCancel}>
          Hủy
        </button>
      </div>
      <ErrorView error={error} />
    </form>
  );
}
