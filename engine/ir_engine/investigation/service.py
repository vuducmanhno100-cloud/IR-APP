import base64
import json
from datetime import datetime, timezone
from enum import Enum
from ipaddress import ip_network

from ..contracts import ErrorCode
from ..errors import ApiError
from ..ingest.identity import get_path, normalize_index
from ..normalization.sysmon import normalize
from .tree import tree_shape


def missing(message="Not found"):
    raise ApiError(ErrorCode.NOT_FOUND, message)


def invalid(message):
    raise ApiError(ErrorCode.VALIDATION_ERROR, message)


def utc_json(value):
    """Every timestamp leaves the engine in UTC with a trailing Z, as the contract says. Rows carry the PostgreSQL
    session time zone, and the embedded cluster takes the Windows one (+07:00 on a machine set to Vietnam)."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: utc_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [utc_json(item) for item in value]
    return value


def cursor_encode(values):
    return base64.urlsafe_b64encode(json.dumps(values, default=str).encode()).decode()


def cursor_decode(value, size):
    try:
        values = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if not isinstance(values, list) or len(values) != size:
            raise ValueError
        if size == 1:
            values[0] = int(values[0])
        else:
            if not isinstance(values[0], str):
                raise ValueError
            timestamp(values[0])
            values[1] = int(values[1])
        if int(values[-1]) < 1:
            raise ValueError
        return values
    except (ValueError, TypeError, UnicodeError):
        invalid("Invalid cursor")


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        invalid("Expected timestamp string with timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except (TypeError, ValueError):
        invalid("Expected timestamp with timezone")


def summary(row):
    payload = row["payload"]
    text = get_path(payload, "dns.question.name") or get_path(payload, "process.command_line") or get_path(payload, "process.executable")
    return dict(
        id=row["id"],
        event_time=row["event_time"],
        host_name=row.get("host_name"),
        event_code=row["event_code"],
        summary=str(text or f"Event {row['event_code']}"),
        process_state=row["process_state"],
        process_id=row.get("process_id"),
    )


EVENT_SELECT = """SELECT r.*,h.host_name,(SELECT min(process_id) FROM event_process_links WHERE event_id=r.id) process_id
                  FROM raw_events r LEFT JOIN hosts h ON h.id=r.host_id"""


def event(conn, event_id):
    row = conn.execute(EVENT_SELECT + " WHERE r.id=%s", (event_id,)).fetchone()
    if not row:
        missing("Event not found")
    try:
        n = normalize(row["payload"])
        normalized = {"process_guid": n.guid, "parent_guid": n.parent_guid, **n.fields}
    except (ValueError, TypeError):
        normalized = {"error": "Invalid process fields"}
    return {
        **summary(row),
        "ingest_time": row["ingest_time"],
        "source": {k: row[k] for k in ("cluster_key", "index_name", "elastic_id")},
        "payload": row["payload"],
        "normalized": normalized,
        "linked_processes": conn.execute(
            "SELECT process_id,link_type,level FROM event_process_links WHERE event_id=%s", (event_id,)
        ).fetchall(),
    }


def search(conn, type, value, match, from_, to, host, event_code, cursor, limit):
    value = value.lower().strip()
    if type == "domain":
        value = value.rstrip(".")
    if type == "process_guid":
        value = value.strip("{}")
    low, high = timestamp(from_), timestamp(to)
    if low and high and low >= high:
        invalid("from must precede to")
    clauses, args = ["t.term_type=%s"], [type]
    literal = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if match == "exact":
        clauses.append("t.term_value=%s")
        args.append(value)
    elif match == "prefix":
        clauses.append("t.term_value LIKE %s")
        args.append(literal + "%")
    elif match == "suffix" and type == "domain":
        rev = ".".join(reversed(value.split(".")))
        clauses.append("(t.term_reversed=%s OR t.term_reversed LIKE %s)")
        args.extend([rev, rev.replace("%", "\\%").replace("_", "\\_") + ".%"])
    elif match == "contains" and type == "command_line" and low and high:
        clauses.append("t.term_value LIKE %s")
        args.append("%" + literal + "%")
    elif match == "cidr" and type == "ip":
        try:
            network = str(ip_network(value, strict=False))
        except ValueError:
            invalid("Invalid CIDR")
        clauses.append("t.term_inet <<= %s::cidr")
        args.append(network)
    else:
        invalid("Unsupported match mode or missing time range")
    for clause, val in [
        ("r.event_time >= %s", low),
        ("r.event_time < %s", high),
        ("h.host_name=%s", host),
        ("r.event_code=%s", event_code),
    ]:
        if val is not None:
            clauses.append(clause)
            args.append(val)
    base = " WHERE EXISTS (SELECT 1 FROM event_search_terms t WHERE t.event_id=r.id AND " + clauses[0]
    # All remaining predicates can refer to the outer event and host.
    base += " AND " + " AND ".join(clauses[1:]) + ")"
    total = conn.execute("SELECT count(*) n FROM raw_events r LEFT JOIN hosts h ON h.id=r.host_id" + base, args).fetchone()["n"]
    if cursor:
        ts, ident = cursor_decode(cursor, 2)
        base += " AND (r.event_time,r.id)<(%s,%s)"
        args.extend([timestamp(ts), int(ident)])
    rows = conn.execute(EVENT_SELECT + base + " ORDER BY r.event_time DESC,r.id DESC LIMIT %s", (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [{**summary(r), "matched_term": {"type": type, "value": value}} for r in rows],
        "next_cursor": cursor_encode([rows[-1]["event_time"], rows[-1]["id"]]) if more else None,
        "total": total,
        "total_is_approximate": False,
    }


def node(conn, row, depth=0, collapsed=0):
    counts = {
        r["link_type"]: r["n"]
        for r in conn.execute("SELECT link_type,count(*) n FROM event_process_links WHERE process_id=%s GROUP BY link_type", (row["id"],))
    }
    keys = (
        "id",
        "host_name",
        "process_guid",
        "pid",
        "image",
        "name",
        "command_line",
        "user_name",
        "started_at",
        "ended_at",
        "is_placeholder",
        "attr_source",
    )
    return {
        **{k: row.get(k) for k in keys},
        "depth": depth,
        "collapsed_children": collapsed,
        "event_counts": {k: counts.get(k, 0) for k in ("network", "dns", "other")},
    }


def trace(conn, event_id=None, process_id=None, max_depth=3, max_nodes=500, include_inferred=False, expand=False):
    empty = dict(
        focus_process_id=None,
        root_id=None,
        nodes=[],
        edges=[],
        matched_event_ids=[event_id] if event_id else [],
        related_events=[],
        candidates=[],
        completeness="MISSING_PARENT",
        warnings=[],
    )
    if event_id is not None:
        event(conn, event_id)
        candidates = conn.execute(
            "SELECT p.*,h.host_name FROM process_instances p JOIN hosts h ON h.id=p.host_id WHERE p.id IN (SELECT process_id FROM event_process_links WHERE event_id=%s)",
            (event_id,),
        ).fetchall()
        if process_id is None and len(candidates) > 1:
            empty.update(candidates=[node(conn, r) for r in candidates], completeness="PENDING_DATA")
            return empty
        if process_id is None:
            if not candidates:
                return empty
            process_id = candidates[0]["id"]
        if process_id not in [r["id"] for r in candidates]:
            missing("Process is not linked to selected event")
    focus = conn.execute("SELECT * FROM process_instances WHERE id=%s", (process_id,)).fetchone()
    if not focus:
        missing("Process not found")
    shape = tree_shape(
        conn, process_id, max_up=0 if expand else 50, max_depth=max_depth, max_nodes=max_nodes, include_inferred=include_inferred
    )
    ids = list(shape.depth_by_id)
    rows = conn.execute(
        "SELECT p.*,h.host_name FROM process_instances p JOIN hosts h ON h.id=p.host_id WHERE p.id=ANY(%s)", (ids,)
    ).fetchall()
    rows.sort(key=lambda r: (shape.depth_by_id[r["id"]], r["id"]))
    root = rows[0]
    edges = conn.execute(
        "SELECT id,parent_id,child_id,level,rule_ver,evidence_event_id FROM process_edges WHERE id=ANY(%s)", (list(shape.edge_ids),)
    ).fetchall()
    edges = [r for r in edges if r["parent_id"] in ids and r["child_id"] in ids]
    unresolved = conn.execute(
        "SELECT 1 FROM unresolved_relations WHERE host_id=%s AND status IN ('OPEN','CONFLICT') AND target_guid=ANY(%s) LIMIT 1",
        (focus["host_id"], [r["process_guid"] for r in rows]),
    ).fetchone()
    pending = conn.execute("SELECT 1 FROM raw_events WHERE host_id=%s AND process_state='PENDING' LIMIT 1", (focus["host_id"],)).fetchone()
    complete = (
        "MISSING_PARENT"
        if root["is_placeholder"] or unresolved
        else "TRUNCATED"
        if shape.truncated
        else "PENDING_DATA"
        if pending
        else "COMPLETE"
    )
    related = conn.execute(
        "SELECT r.*,l.process_id,l.link_type FROM raw_events r JOIN event_process_links l ON l.event_id=r.id WHERE l.process_id=ANY(%s) ORDER BY r.event_time DESC,r.id DESC LIMIT 200",
        (ids,),
    ).fetchall()
    return {
        **empty,
        "focus_process_id": process_id,
        "root_id": root["id"],
        "nodes": [node(conn, r, shape.depth_by_id[r["id"]], shape.collapsed[r["id"]]) for r in rows],
        "edges": edges,
        "related_events": [
            dict(
                event_id=r["id"],
                process_id=r["process_id"],
                link_type=r["link_type"],
                event_time=r["event_time"],
                event_code=r["event_code"],
                summary=summary(r)["summary"],
            )
            for r in related
        ],
        "completeness": complete,
        "warnings": ["Coverage is limited to locally collected data."],
    }


def alerts(conn, severity=None, status=None, from_=None, to=None, cursor=None, limit=100):
    clauses = ["true"]
    args = []
    for field, val, op in [
        ("a.severity", severity, "="),
        ("a.status", status, "="),
        ("a.alert_time", timestamp(from_), ">="),
        ("a.alert_time", timestamp(to), "<"),
    ]:
        if val is not None:
            clauses.append(field + op + "%s")
            args.append(val)
    if cursor:
        ts, ident = cursor_decode(cursor, 2)
        clauses.append("(a.alert_time,a.event_id)<(%s,%s)")
        args.extend([timestamp(ts), int(ident)])
    rows = conn.execute(
        "SELECT a.*,h.host_name,r.cluster_key FROM alert_records a JOIN raw_events r ON r.id=a.event_id LEFT JOIN hosts h ON h.id=r.host_id WHERE "
        + " AND ".join(clauses)
        + " ORDER BY a.alert_time DESC,a.event_id DESC LIMIT %s",
        (*args, limit + 1),
    ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        row["original_event_id"] = None
        for ref in row.pop("original_event_ref") or []:
            found = conn.execute(
                "SELECT id FROM raw_events WHERE cluster_key=%s AND index_name=%s AND elastic_id=%s",
                (row["cluster_key"], normalize_index(ref.get("index", "")), ref.get("id")),
            ).fetchone()
            if found:
                row["original_event_id"] = found["id"]
                break
        row.pop("cluster_key")
    return dict(items=rows, next_cursor=cursor_encode([rows[-1]["alert_time"], rows[-1]["event_id"]]) if more else None)


def unresolved_list(conn, status=None, cursor=None, limit=100):
    clauses = ["u.status IN ('OPEN','CONFLICT')"]
    args = []
    if status:
        if status not in ("OPEN", "CONFLICT"):
            invalid("Invalid unresolved status")
        clauses.append("u.status=%s")
        args.append(status)
    if cursor:
        clauses.append("u.id<%s")
        args.append(int(cursor_decode(cursor, 1)[0]))
    rows = conn.execute(
        "SELECT u.*,h.host_name FROM unresolved_relations u LEFT JOIN hosts h ON h.id=u.host_id WHERE "
        + " AND ".join(clauses)
        + " ORDER BY u.id DESC LIMIT %s",
        (*args, limit + 1),
    ).fetchall()
    return dict(items=rows[:limit], next_cursor=cursor_encode([rows[limit - 1]["id"]]) if len(rows) > limit else None)
