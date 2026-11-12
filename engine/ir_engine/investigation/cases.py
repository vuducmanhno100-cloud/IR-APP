import hashlib
import html
import json
import os
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from .. import audit
from .service import cursor_decode, cursor_encode, event, invalid, missing


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
        snapshot = body.snapshot.model_dump(mode="json") if body.snapshot else None
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
                evidence.append(conn.execute("SELECT * FROM process_instances WHERE id=%s", (item["ref_id"],)).fetchone())
            else:
                evidence.append(item["snapshot"])
        content = (
            '<!doctype html><html lang="vi"><meta charset="utf-8"><title>Incident case</title>'
            "<h1>"
            + html.escape(data["title"])
            + "</h1><p>Scope: locally collected evidence. Missing data may remain.</p><pre>"
            + html.escape(json.dumps({"case": data, "evidence": evidence}, ensure_ascii=False, default=str, indent=2))
            + "</pre></html>"
        ).encode()
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
