import psycopg
from psycopg.rows import dict_row

from ..ingest.identity import get_path
from ..normalization.sysmon import normalize
from ..runtime.services import BackgroundService

PARSER_VERSION = 1
RULE_VERSION = "sysmon-eid1-v1"


def run_once(conn: psycopg.Connection, batch_size: int = 500) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT * FROM raw_events WHERE process_state='PENDING' ORDER BY ingest_time NULLS LAST,id LIMIT %s FOR UPDATE SKIP LOCKED",
            (batch_size,),
        ).fetchall()
        for ev in rows:
            try:
                with conn.transaction():
                    process_event(cur, ev)
            except (psycopg.DataError, psycopg.IntegrityError, ValueError, TypeError) as exc:
                cur.execute(
                    "UPDATE raw_events SET process_state='FAILED',parse_version=%s,error=%s WHERE id=%s",
                    (PARSER_VERSION, type(exc).__name__, ev["id"]),
                )
    return len(rows)


def process_event(cur, ev):
    n = normalize(ev["payload"])
    for kind, value in n.terms:
        cur.execute(
            "INSERT INTO event_search_terms(event_id,term_type,term_value,term_reversed,term_inet,event_time) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                ev["id"],
                kind,
                value,
                ".".join(reversed(value.split("."))) if kind == "domain" else None,
                value if kind == "ip" else None,
                ev["event_time"],
            ),
        )
    state = "UNLINKED" if n.supported else "SKIPPED"
    if n.supported and n.guid and ev["host_id"] is not None:
        process = upsert(cur, ev, n.guid, n.fields, n.code == "1")
        link = {"1": "create", "3": "network", "5": "terminate", "22": "dns"}.get(n.code, "other")
        cur.execute(
            "INSERT INTO event_process_links VALUES (%s,%s,%s,'VERIFIED','guid-match') ON CONFLICT DO NOTHING",
            (ev["id"], process["id"], link),
        )
        if n.code == "1":
            cur.execute(
                "UPDATE unresolved_relations SET status='RESOLVED',updated_at=now() WHERE host_id=%s AND target_guid=%s AND status='OPEN'",
                (ev["host_id"], n.guid),
            )
            if n.parent_guid:
                parent = upsert(cur, ev, n.parent_guid, n.parent_fields, False, "child_event")
                if parent["is_placeholder"]:
                    unresolved(cur, ev, n.parent_guid, "parent_create_missing")
                cycle = cur.execute(
                    "WITH RECURSIVE a(id) AS (SELECT %s::bigint UNION SELECT e.parent_id "
                    "FROM process_edges e JOIN a ON e.child_id=a.id) SELECT 1 FROM a WHERE id=%s",
                    (parent["id"], process["id"]),
                ).fetchone()
                existing = cur.execute(
                    "SELECT parent_id FROM process_edges WHERE child_id=%s AND level='VERIFIED'", (process["id"],)
                ).fetchone()
                if cycle or (existing and existing["parent_id"] != parent["id"]):
                    unresolved(cur, ev, n.parent_guid, "cycle_or_parent_conflict", "CONFLICT")
                else:
                    cur.execute(
                        "INSERT INTO process_edges(parent_id,child_id,evidence_event_id,level,rule_ver) "
                        "VALUES (%s,%s,%s,'VERIFIED',%s) ON CONFLICT DO NOTHING",
                        (parent["id"], process["id"], ev["id"], RULE_VERSION),
                    )
        elif process["is_placeholder"]:
            unresolved(cur, ev, n.guid, "process_create_missing")
        if n.code == "5":
            if process["started_at"] is None or ev["event_time"] >= process["started_at"]:
                cur.execute(
                    "UPDATE process_instances SET ended_at=%s,end_event_id=%s WHERE id=%s",
                    (ev["event_time"], ev["id"], process["id"]),
                )
            else:
                unresolved(cur, ev, n.guid, "termination_before_create", "CONFLICT")
        if n.code == "1":
            validate_edges(cur, process["id"])
        state = "CORRELATED"
    if state == "UNLINKED":
        unresolved(cur, ev, n.guid, "missing_process_identity", relation="process_link")
    cur.execute("UPDATE raw_events SET process_state=%s,parse_version=%s,error=NULL WHERE id=%s", (state, PARSER_VERSION, ev["id"]))


def unresolved(cur, ev, target, reason, status="OPEN", relation="parent"):
    cur.execute(
        "INSERT INTO unresolved_relations(event_id,host_id,relation,target_guid,reason,status) "
        "SELECT %s,%s,%s,%s,%s,%s WHERE NOT EXISTS "
        "(SELECT 1 FROM unresolved_relations WHERE event_id=%s AND reason=%s)",
        (ev["id"], ev["host_id"], relation, target, reason, status, ev["id"], reason),
    )


def upsert(cur, ev, guid, fields, own, source="self"):
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (ev["host_id"],))
    current = cur.execute(
        "SELECT * FROM process_instances WHERE host_id=%s AND process_guid=%s FOR UPDATE", (ev["host_id"], guid)
    ).fetchone()
    if current and not current["is_placeholder"]:
        if own and (
            current["started_at"] != ev["event_time"]
            or any(
                current.get(key) is not None and fields.get(key) is not None and current[key] != fields[key]
                for key in ("pid", "image", "command_line")
            )
        ):
            unresolved(cur, ev, guid, "process_create_conflict", "CONFLICT")
        return current
    values = (
        fields.get("pid"),
        fields.get("image"),
        fields.get("name"),
        fields.get("command_line"),
        fields.get("user_name"),
        fields.get("integrity_level"),
        ev["event_time"] if own else None,
        not own,
        "self" if own else source,
        ev["id"] if own else None,
    )
    if current:
        if own:
            if current["ended_at"] and current["ended_at"] < ev["event_time"]:
                unresolved(cur, ev, guid, "termination_before_create", "CONFLICT")
                cur.execute("UPDATE process_instances SET ended_at=NULL,end_event_id=NULL WHERE id=%s", (current["id"],))
            return cur.execute(
                "UPDATE process_instances SET pid=%s,image=%s,name=%s,command_line=%s,user_name=%s,integrity_level=%s,"
                "started_at=%s,is_placeholder=%s,attr_source=%s,create_event_id=%s WHERE id=%s RETURNING *",
                (*values, current["id"]),
            ).fetchone()
        return current
    return cur.execute(
        "INSERT INTO process_instances(pid,image,name,command_line,user_name,integrity_level,started_at,is_placeholder,"
        "attr_source,create_event_id,host_id,process_guid) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (*values, ev["host_id"], guid),
    ).fetchone()


class CorrelationWorker(BackgroundService):
    name = "correlation"
    interval_s = 2.0

    def __init__(self, db, batch_size: int = 500):
        super().__init__(db)
        self.batch_size = batch_size

    def tick(self) -> bool:
        with self.db.connection() as conn:
            processed = run_once(conn, self.batch_size)
        return processed > 0


def validate_edges(cur, process_id):
    rows = cur.execute(
        "SELECT e.id,e.evidence_event_id,c.host_id,c.process_guid,p.started_at parent_start,c.started_at child_start,"
        "pr.payload parent_payload,cr.payload child_payload FROM process_edges e "
        "JOIN process_instances p ON p.id=e.parent_id JOIN process_instances c ON c.id=e.child_id "
        "LEFT JOIN raw_events pr ON pr.id=p.create_event_id LEFT JOIN raw_events cr ON cr.id=c.create_event_id "
        "WHERE e.level='VERIFIED' AND (e.parent_id=%s OR e.child_id=%s)",
        (process_id, process_id),
    ).fetchall()
    for row in rows:
        reason = None
        if row["parent_start"] and row["child_start"] and row["parent_start"] > row["child_start"]:
            reason = "parent_created_after_child"
        parent_boot = get_path(row["parent_payload"], "host.boot.id")
        child_boot = get_path(row["child_payload"], "host.boot.id")
        if parent_boot and child_boot and parent_boot != child_boot:
            reason = "boot_conflict"
        if reason and row["evidence_event_id"]:
            ev = {"id": row["evidence_event_id"], "host_id": row["host_id"]}
            unresolved(cur, ev, row["process_guid"], reason, "CONFLICT")
            cur.execute("DELETE FROM process_edges WHERE id=%s", (row["id"],))
