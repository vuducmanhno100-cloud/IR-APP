import hashlib
import html
import json
import os
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from .. import audit
from .service import cursor_decode, cursor_encode, event, invalid, missing, utc_json


def get(conn, case_id):
    row = conn.execute("SELECT * FROM incident_cases WHERE id=%s", (case_id,)).fetchone()
    if not row:
        missing("Case not found")
    row["items"] = conn.execute("SELECT * FROM case_items WHERE case_id=%s ORDER BY id", (case_id,)).fetchall()
    row["files"] = conn.execute("SELECT * FROM evidence_files WHERE case_id=%s ORDER BY id", (case_id,)).fetchall()
    return row


def listing(conn, cursor=None, limit=100):
    args = []
    where = ""
    if cursor:
        where = " WHERE id<%s"
        args.append(int(cursor_decode(cursor, 1)[0]))
    rows = conn.execute("SELECT * FROM incident_cases" + where + " ORDER BY id DESC LIMIT %s", (*args, limit + 1)).fetchall()
    return dict(items=rows[:limit], next_cursor=cursor_encode([rows[limit - 1]["id"]]) if len(rows) > limit else None)


def create(conn, body):
    with conn.transaction():
        row = conn.execute("INSERT INTO incident_cases(title,notes) VALUES (%s,%s) RETURNING *", (body.title, body.notes)).fetchone()
        audit.record(conn, "case.create", str(row["id"]))
    return row


def update(conn, case_id, body):
    with conn.transaction():
        current = get(conn, case_id)
        data = body.model_dump(exclude_unset=True)
        if data.get("title", current["title"]) is None or data.get("status", current["status"]) is None:
            invalid("Title and status cannot be null")
        row = conn.execute(
            "UPDATE incident_cases SET title=%s,status=%s,notes=%s,updated_at=now() WHERE id=%s RETURNING *",
            (data.get("title", current["title"]), data.get("status", current["status"]), data.get("notes", current["notes"]), case_id),
        ).fetchone()
        audit.record(conn, "case.update", str(case_id))
    return row


def add(conn, case_id, body):
    with conn.transaction():
        get(conn, case_id)
        # Stored exactly as /trace returns it, so the pinned tree is the one the analyst saw.
        snapshot = utc_json(body.snapshot.model_dump()) if body.snapshot else None
        if body.item_type == "tree_snapshot":
            if snapshot is None:
                invalid("Tree snapshot is required")
        elif body.ref_id is None:
            invalid("Reference is required")
        elif body.item_type == "event":
            event(conn, body.ref_id)
        elif not conn.execute("SELECT 1 FROM process_instances WHERE id=%s", (body.ref_id,)).fetchone():
            missing("Process not found")
        row = conn.execute(
            "INSERT INTO case_items(case_id,item_type,ref_id,snapshot,note) VALUES (%s,%s,%s,%s,%s) RETURNING *",
            (case_id, body.item_type, body.ref_id, Jsonb(snapshot) if snapshot else None, body.note),
        ).fetchone()
        audit.record(conn, "case.item.add", str(case_id), {"item_id": row["id"]})
    return row


def remove(conn, case_id, item_id):
    with conn.transaction():
        get(conn, case_id)
        row = conn.execute("DELETE FROM case_items WHERE case_id=%s AND id=%s RETURNING id", (case_id, item_id)).fetchone()
        if not row:
            missing("Case item not found")
        audit.record(conn, "case.item.remove", str(case_id), {"item_id": item_id})


def export(conn, case_id, body, data_dir=None):
    if body.get("format") != "html":
        invalid("Only HTML export is supported")
    with conn.transaction():
        data = get(conn, case_id)
        evidence = []
        for item in data["items"]:
            if item["item_type"] == "event":
                evidence.append(event(conn, item["ref_id"]))
            elif item["item_type"] == "process":
                evidence.append(
                    conn.execute(
                        "SELECT p.*,h.host_name FROM process_instances p LEFT JOIN hosts h ON h.id=p.host_id WHERE p.id=%s",
                        (item["ref_id"],),
                    ).fetchone()
                )
            else:
                evidence.append(item["snapshot"])
        content = render_report(utc_json(data), [utc_json(e) for e in evidence]).encode()
        data_root = (
            Path(data_dir) if data_dir is not None else Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "IRDesktop"
        )
        root = data_root / "exports"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"case-{case_id}-{uuid4().hex}.html"
        try:
            with path.open("xb") as stream:
                stream.write(content)
            path.chmod(0o600)
            row = conn.execute(
                "INSERT INTO evidence_files(case_id,artifact_path,sha256) VALUES (%s,%s,%s) RETURNING *",
                (case_id, str(path), hashlib.sha256(content).hexdigest()),
            ).fetchone()
            audit.record(conn, "case.export", str(case_id), {"file_id": row["id"], "sha256": row["sha256"]})
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return row


STATUS_LABELS = {"OPEN": "Mở", "IN_PROGRESS": "Đang xử lý", "CLOSED": "Đã đóng"}
REPORT_STYLE = (
    "body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#13263d}h1{margin-bottom:4px}"
    "table{border-collapse:collapse;width:100%;margin:8px 0 16px}th,td{border:1px solid #d5dde8;padding:6px 8px;"
    "text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#f2f5f9;width:22%}"
    ".item{border:1px solid #d5dde8;border-radius:6px;padding:12px 16px;margin:16px 0}.muted{color:#5b6b80}"
    "code,pre{font-family:Consolas,monospace;font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
    "ul.tree{list-style:none;padding-left:18px;border-left:1px dashed #b8c4d4}"
)


def _text(value):
    return "" if value is None else html.escape(str(value))


def _rows(pairs):
    return (
        "<table>" + "".join(f"<tr><th>{html.escape(k)}</th><td>{_text(v)}</td></tr>" for k, v in pairs if v not in (None, "")) + "</table>"
    )


def _raw(value):
    return (
        "<details><summary>Dữ liệu gốc</summary><pre>"
        + html.escape(json.dumps(value, ensure_ascii=False, default=str, indent=2))
        + "</pre></details>"
    )


def _render_event(e):
    if e is None:
        return "<p class=muted>Event không còn trong cơ sở dữ liệu.</p>"
    source = e.get("source") or {}
    return _rows(
        [
            ("Thời gian (UTC)", e.get("event_time")),
            ("Host", e.get("host_name")),
            ("Mã event", e.get("event_code")),
            ("Tóm tắt", e.get("summary")),
            ("Process (id)", e.get("process_id")),
            ("Nguồn", f"{source.get('index_name', '')} / {source.get('elastic_id', '')}"),
        ]
    ) + _raw(e.get("payload"))


def _render_process(p):
    if p is None:
        return "<p class=muted>Process không còn trong cơ sở dữ liệu.</p>"
    return _rows(
        [
            ("Tên", p.get("name")),
            ("PID", p.get("pid")),
            ("Host", p.get("host_name")),
            ("Image", p.get("image")),
            ("Command line", p.get("command_line")),
            ("User", p.get("user_name")),
            ("Bắt đầu (UTC)", p.get("started_at")),
            ("Kết thúc (UTC)", p.get("ended_at")),
            ("Process GUID", p.get("process_guid")),
        ]
    )


def _render_tree(snapshot):
    if not snapshot or not snapshot.get("nodes"):
        return "<p class=muted>Ảnh chụp cây trống.</p>"
    nodes = {n["id"]: n for n in snapshot["nodes"]}
    children = {}
    for edge in snapshot.get("edges", []):
        children.setdefault(edge["parent_id"], []).append((edge["child_id"], edge.get("level")))
    seen = set()

    def branch(node_id, level=None):
        if node_id in seen or node_id not in nodes:
            return ""
        seen.add(node_id)
        n = nodes[node_id]
        label = f"{n.get('name') or 'không rõ'} (pid {n.get('pid') if n.get('pid') is not None else '?'})"
        detail = n.get("command_line") or n.get("image") or ""
        marker = f" [{level}]" if level and level != "VERIFIED" else ""
        kids = "".join(branch(child, lvl) for child, lvl in children.get(node_id, []))
        return (
            f"<li><strong>{_text(label)}</strong>{_text(marker)} <code>{_text(detail)}</code>"
            + (f"<ul class=tree>{kids}</ul>" if kids else "")
            + "</li>"
        )

    root = snapshot.get("root_id")
    body = branch(root) if root in nodes else ""
    body += "".join(branch(node_id) for node_id in nodes if node_id not in seen)
    return f"<p class=muted>Độ đầy đủ khi chụp: {_text(snapshot.get('completeness'))}</p><ul class=tree>{body}</ul>"


def render_report(data, evidence):
    """Readable incident report; every value is escaped because it comes from collected logs."""
    renderers = {"event": _render_event, "process": _render_process, "tree_snapshot": _render_tree}
    titles = {"event": "Event", "process": "Process", "tree_snapshot": "Cây process"}
    items = "".join(
        f"<section class=item><h3>{index}. {titles[item['item_type']]}"
        + (f" #{item['ref_id']}" if item.get("ref_id") is not None else "")
        + f"</h3><p class=muted>Ghim lúc {_text(item.get('pinned_at'))} (UTC)</p>"
        + (f"<p><strong>Ghi chú:</strong> {_text(item['note'])}</p>" if item.get("note") else "")
        + renderers[item["item_type"]](value)
        + "</section>"
        for index, (item, value) in enumerate(zip(data["items"], evidence, strict=True), start=1)
    )
    return (
        f'<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Case {_text(data["title"])}</title>'
        f"<style>{REPORT_STYLE}</style></head><body>"
        f"<h1>{_text(data['title'])}</h1>"
        + _rows(
            [
                ("Mã case", data["id"]),
                ("Trạng thái", STATUS_LABELS.get(data["status"], data["status"])),
                ("Tạo lúc (UTC)", data["created_at"]),
                ("Cập nhật (UTC)", data["updated_at"]),
                ("Ghi chú", data.get("notes")),
                ("Số bằng chứng", len(data["items"])),
            ]
        )
        + "<p class=muted>Phạm vi: chỉ gồm dữ liệu đã đồng bộ về máy điều tra; dữ liệu ngoài khoảng đồng bộ có thể thiếu.</p>"
        + f"<h2>Bằng chứng</h2>{items or '<p class=muted>Chưa có bằng chứng.</p>'}"
        + "<h2>Dữ liệu đầy đủ</h2>"
        + _raw({"case": data, "evidence": evidence})
        + "</body></html>"
    )
