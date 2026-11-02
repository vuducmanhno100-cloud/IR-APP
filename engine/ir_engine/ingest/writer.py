from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb

from ..contracts import SourceKind
from .identity import canonical_key, get_path, host_key, normalize_index, parse_ts


@dataclass(frozen=True)
class SourceRef:
    id: int
    cluster_key: str
    kind: SourceKind


@dataclass
class WriteStats:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    duplicates: int = 0
    indices: dict = field(default_factory=dict)


UPSERT_HOST = """
INSERT INTO hosts (host_key, host_name, agent_id, first_seen, last_seen)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (host_key) DO UPDATE SET
    host_name = COALESCE(EXCLUDED.host_name, hosts.host_name),
    agent_id = COALESCE(EXCLUDED.agent_id, hosts.agent_id),
    first_seen = LEAST(hosts.first_seen, EXCLUDED.first_seen),
    last_seen = GREATEST(hosts.last_seen, EXCLUDED.last_seen)
RETURNING id
"""


@dataclass
class _HostSeen:
    first: object
    last: object
    name: object = None
    agent: object = None


def _upsert_hosts(cur: psycopg.Cursor, hits: list[dict]) -> dict[str, int]:
    """One upsert per distinct host in the batch instead of one per hit.

    The resulting row is the same as upserting hit by hit: LEAST/GREATEST are order-free and the last non-null
    name/agent in batch order wins, exactly like successive COALESCE(EXCLUDED.x, hosts.x)."""
    seen: dict[str, _HostSeen] = {}
    for hit in hits:
        src = hit["_source"]
        hkey, ts = host_key(src), parse_ts(get_path(src, "@timestamp"))
        if not (hkey and ts):
            continue
        entry = seen.get(hkey)
        if entry is None:
            entry = seen[hkey] = _HostSeen(ts, ts)
        entry.first, entry.last = min(entry.first, ts), max(entry.last, ts)
        name, agent = get_path(src, "host.name"), get_path(src, "agent.id")
        entry.name = name if name is not None else entry.name
        entry.agent = agent if agent is not None else entry.agent
    ids = {}
    for hkey, entry in seen.items():
        row = cur.execute(UPSERT_HOST, (hkey, entry.name, entry.agent, entry.first, entry.last)).fetchone()
        ids[hkey] = row["id"] if isinstance(row, dict) else row[0]
    return ids


def _base_values(source: SourceRef, hit: dict, host_id, ckey) -> dict:
    src = hit["_source"]
    event_time = parse_ts(get_path(src, "@timestamp"))
    if event_time is None:
        raise ValueError(f"document {hit.get('_id')} has no @timestamp")
    return {
        "source_id": source.id,
        "cluster_key": source.cluster_key,
        "index_name": normalize_index(hit["_index"]),
        "elastic_id": hit["_id"],
        "canonical_key": ckey,
        "host_id": host_id,
        "event_code": None if get_path(src, "event.code") is None else str(get_path(src, "event.code")),
        "event_kind": get_path(src, "event.kind"),
        "event_time": event_time,
        "ingest_time": parse_ts(get_path(src, "event.ingested")),
        "created_time": parse_ts(get_path(src, "event.created")),
        "payload": Jsonb(src),
        "process_state": "PENDING" if source.kind == SourceKind.LOG else "SKIPPED",
    }


INSERT_LOG = """
INSERT INTO raw_events (source_id, cluster_key, index_name, elastic_id, canonical_key, host_id, event_code,
                        event_kind, event_time, ingest_time, created_time, payload, process_state)
VALUES (%(source_id)s, %(cluster_key)s, %(index_name)s, %(elastic_id)s, %(canonical_key)s, %(host_id)s,
        %(event_code)s, %(event_kind)s, %(event_time)s, %(ingest_time)s, %(created_time)s, %(payload)s,
        %(process_state)s)
ON CONFLICT DO NOTHING
RETURNING id
"""

UPSERT_ALERT_EVENT = """
INSERT INTO raw_events (source_id, cluster_key, index_name, elastic_id, canonical_key, host_id, event_code,
                        event_kind, event_time, ingest_time, created_time, payload, process_state)
VALUES (%(source_id)s, %(cluster_key)s, %(index_name)s, %(elastic_id)s, %(canonical_key)s, %(host_id)s,
        %(event_code)s, %(event_kind)s, %(event_time)s, %(ingest_time)s, %(created_time)s, %(payload)s,
        %(process_state)s)
ON CONFLICT (cluster_key, index_name, elastic_id) DO UPDATE SET
    payload = EXCLUDED.payload,
    event_time = EXCLUDED.event_time,
    local_received_at = now()
RETURNING id, (xmax = 0) AS inserted
"""

UPSERT_ALERT = """
INSERT INTO alert_records (event_id, source_alert_id, rule_id, rule_name, severity, risk_score, status,
                           status_updated_at, alert_time, original_time, original_event_ref)
VALUES (%(event_id)s, %(source_alert_id)s, %(rule_id)s, %(rule_name)s, %(severity)s, %(risk_score)s,
        %(status)s, %(status_updated_at)s, %(alert_time)s, %(original_time)s, %(original_event_ref)s)
ON CONFLICT (event_id) DO UPDATE SET
    status = EXCLUDED.status,
    status_updated_at = EXCLUDED.status_updated_at,
    severity = EXCLUDED.severity,
    risk_score = EXCLUDED.risk_score
"""


def _alert_values(event_id: int, hit: dict) -> dict:
    src = hit["_source"]
    ancestors = get_path(src, "kibana.alert.ancestors") or []
    return {
        "event_id": event_id,
        "source_alert_id": get_path(src, "kibana.alert.uuid") or hit["_id"],
        "rule_id": get_path(src, "kibana.alert.rule.uuid"),
        "rule_name": get_path(src, "kibana.alert.rule.name"),
        "severity": get_path(src, "kibana.alert.severity"),
        "risk_score": get_path(src, "kibana.alert.risk_score"),
        "status": get_path(src, "kibana.alert.workflow_status") or "open",
        "status_updated_at": parse_ts(get_path(src, "kibana.alert.workflow_status_updated_at")),
        "alert_time": parse_ts(get_path(src, "@timestamp")),
        "original_time": parse_ts(get_path(src, "kibana.alert.original_time")),
        "original_event_ref": Jsonb(ancestors if isinstance(ancestors, list) else [ancestors]),
    }


def _insert_logs(cur: psycopg.Cursor, rows: list[dict]) -> int:
    """Pipelined: one network round trip for the whole batch. Rows are applied in order, so a duplicate inside
    the batch is skipped exactly as before. Returns how many rows were new."""
    cur.executemany(INSERT_LOG, rows, returning=True)
    inserted = 0
    while True:
        if cur.fetchone() is not None:
            inserted += 1
        if not cur.nextset():
            return inserted


def write_hits(conn: psycopg.Connection, source: SourceRef, hits: list[dict]) -> WriteStats:
    stats = WriteStats()
    with conn.transaction(), conn.cursor() as cur:
        host_ids = _upsert_hosts(cur, hits)
        logs = []
        for hit in hits:
            stats.fetched += 1
            index = normalize_index(hit["_index"])
            stats.indices[index] = stats.indices.get(index, 0) + 1
            src = hit["_source"]
            hkey = host_key(src)
            host_id = host_ids.get(hkey) if hkey else None
            if source.kind == SourceKind.LOG:
                logs.append(_base_values(source, hit, host_id, canonical_key(src, hkey)))
                continue
            values = _base_values(source, hit, host_id, None)
            row = cur.execute(UPSERT_ALERT_EVENT, values).fetchone()
            event_id = row["id"] if isinstance(row, dict) else row[0]
            inserted = row["inserted"] if isinstance(row, dict) else row[1]
            cur.execute(UPSERT_ALERT, _alert_values(event_id, hit))
            if inserted:
                stats.inserted += 1
            else:
                stats.updated += 1
        if logs:
            stats.inserted = _insert_logs(cur, logs)
            stats.duplicates = len(logs) - stats.inserted
    return stats
