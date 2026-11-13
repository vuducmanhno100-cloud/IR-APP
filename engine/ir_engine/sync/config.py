from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from ..contracts import ConnectionState, ErrorCode, SourceState
from ..credentials import elk_ref
from ..elk.probe import suggest_sources
from ..errors import ApiError
from .common import iso, parse_iso

SETTING_DEFAULTS = {
    "sync_interval_s": 300,
    "quota_bytes": 32212254720,
    "retention_days": 90,
    "reconcile_window_h": 24,
    "alert_resync_days": 7,
}
ACTIVE_CONNECTION = "active_connection_id"
SYNC_PAUSED = "sync_paused"

CONNECTION_FIELDS = (
    "id, url, cluster_key, cluster_uuid, cluster_name, es_version, credential_ref, api_key_id, api_key_name, "
    "api_key_expires_at, ca_pem, ca_fingerprint, state"
)
SOURCE_FIELDS = "id, connection_id, name, kind, index_patterns, timestamp_strategy, timestamp_field, overlap_s, safety_lag_s, batch_size, state, enabled"
SOURCE_DEFAULTS = {"overlap_s": 120, "safety_lag_s": 60, "batch_size": 1000, "enabled": True}


def plan_key(source_id: int) -> str:
    return f"bootstrap.{source_id}"


def streams_key(source_id: int) -> str:
    return f"streams.{source_id}"


def get_value(conn: psycopg.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    return default if row is None else row["value"]


def set_value(conn: psycopg.Connection, key: str, value) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        (key, Jsonb(value)),
    )


def delete_value(conn: psycopg.Connection, key: str) -> None:
    conn.execute("DELETE FROM app_settings WHERE key = %s", (key,))


def get_settings(conn: psycopg.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM app_settings WHERE key = ANY(%s)", (list(SETTING_DEFAULTS),)).fetchall()
    found = {r["key"]: r["value"] for r in rows}
    return {key: int(found.get(key, default)) for key, default in SETTING_DEFAULTS.items()}


def update_settings(conn: psycopg.Connection, values: dict) -> dict:
    for key in SETTING_DEFAULTS:
        set_value(conn, key, int(values[key]))
    return get_settings(conn)


def get_plan(conn: psycopg.Connection, source_id: int) -> dict | None:
    """A bootstrap plan; `auto` marks the 24 hours planned by the first sync rather than asked for by the user."""
    plan = get_value(conn, plan_key(source_id))
    if not plan:
        return None
    return {"from": parse_iso(plan["from"]), "to": parse_iso(plan["to"]), "next": parse_iso(plan["next"]), "auto": bool(plan.get("auto"))}


def set_plan(conn: psycopg.Connection, source_id: int, lower: datetime, upper: datetime, next_at: datetime, auto: bool = False) -> None:
    set_value(conn, plan_key(source_id), {"from": iso(lower), "to": iso(upper), "next": iso(next_at), "auto": auto})


PLAN_LOCK = 7301


def lock_plans(conn: psycopg.Connection, source_ids: list[int]) -> None:
    """Serialize everything that reads and writes a source's bootstrap plan, until the transaction ends.

    The first sync writes the checkpoint then the automatic plan, POST /sync/bootstrap wrote the plan then the
    checkpoint: run together they deadlocked, or the automatic plan overwrote the user's. Ids are locked in order."""
    for source_id in sorted(set(source_ids)):
        conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (PLAN_LOCK, source_id))


def clear_plan(conn: psycopg.Connection, source_id: int) -> None:
    delete_value(conn, plan_key(source_id))


def active_connection(conn: psycopg.Connection) -> dict | None:
    connection_id = get_value(conn, ACTIVE_CONNECTION)
    if connection_id is None:
        return None
    return conn.execute(f"SELECT {CONNECTION_FIELDS} FROM elk_connections WHERE id = %s", (connection_id,)).fetchone()


def require_connection(conn: psycopg.Connection) -> dict:
    row = active_connection(conn)
    if row is None:
        raise ApiError(ErrorCode.NOT_CONFIGURED, "ELK connection is not configured")
    return row


def connection_view(row: dict) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "cluster_key": row["cluster_key"],
        "cluster_name": row["cluster_name"],
        "cluster_uuid": row["cluster_uuid"],
        "es_version": row["es_version"],
        "api_key_id": row["api_key_id"],
        "api_key_name": row["api_key_name"],
        "api_key_expires_at": iso(row["api_key_expires_at"]),
        "ca_fingerprint": row["ca_fingerprint"],
        "state": row["state"],
    }


def set_connection_state(conn: psycopg.Connection, connection_id: int, state: ConnectionState) -> None:
    conn.execute("UPDATE elk_connections SET state = %s, updated_at = now() WHERE id = %s", (state.value, connection_id))


def save_connection(conn: psycopg.Connection, store, target, check) -> tuple[dict, dict | None]:
    """Store the key, upsert the connection by cluster identity and make it the active one.

    Same cluster as before keeps its sources and checkpoints; a different cluster gets its own rows."""
    previous = active_connection(conn)
    cluster = check.cluster
    ref = elk_ref(cluster["cluster_key"])
    store.set(ref, target.api_key)
    row = conn.execute(
        f"""
        INSERT INTO elk_connections (url, cluster_key, cluster_uuid, cluster_name, es_version, credential_ref, api_key_id,
                                     api_key_name, api_key_expires_at, ca_pem, ca_fingerprint, state)
        VALUES (%(url)s, %(cluster_key)s, %(uuid)s, %(name)s, %(version)s, %(ref)s, %(key_id)s, %(key_name)s, %(expires)s,
                %(ca_pem)s, %(ca_fingerprint)s, 'ACTIVE')
        ON CONFLICT (cluster_key) DO UPDATE SET
            url = EXCLUDED.url,
            cluster_uuid = EXCLUDED.cluster_uuid,
            cluster_name = COALESCE(EXCLUDED.cluster_name, elk_connections.cluster_name),
            es_version = COALESCE(EXCLUDED.es_version, elk_connections.es_version),
            credential_ref = EXCLUDED.credential_ref,
            api_key_id = EXCLUDED.api_key_id,
            api_key_name = EXCLUDED.api_key_name,
            api_key_expires_at = EXCLUDED.api_key_expires_at,
            ca_pem = EXCLUDED.ca_pem,
            ca_fingerprint = EXCLUDED.ca_fingerprint,
            state = 'ACTIVE',
            updated_at = now()
        RETURNING {CONNECTION_FIELDS}
        """,
        {
            "url": target.url,
            "cluster_key": cluster["cluster_key"],
            "uuid": cluster["uuid"],
            "name": cluster["name"],
            "version": cluster["version"],
            "ref": ref,
            "key_id": check.authenticated.get("api_key_id"),
            "key_name": check.authenticated.get("api_key_name"),
            "expires": check.api_key_expires_at,
            "ca_pem": target.ca_pem,
            "ca_fingerprint": target.ca_fingerprint,
        },
    ).fetchone()
    sources = list_sources(conn, row["id"])
    for source in sources:
        delete_value(conn, streams_key(source["id"]))
        if source["state"] in (SourceState.AUTH_FAILED, SourceState.FORBIDDEN, SourceState.ERROR):
            set_source_state(conn, source["id"], SourceState.ACTIVE)
    apply_wizard_sources(conn, row["id"], sources, suggest_sources(target.log_patterns, target.alert_patterns, check.ingested_present))
    set_value(conn, ACTIVE_CONNECTION, row["id"])
    if previous and previous["id"] != row["id"]:
        store.delete(previous["credential_ref"])
        cancel_queued(conn, [s["id"] for s in list_sources(conn, previous["id"])], "Đã chuyển sang kết nối ELK khác")
    return row, previous


def apply_wizard_sources(conn: psycopg.Connection, connection_id: int, sources: list[dict], specs: list[dict]) -> None:
    """The wizard owns the index patterns of the sources it creates (one for logs, one for alerts).

    Saving again with other patterns used to keep the old ones silently. Checkpoints stay: indices added by a new
    pattern are read from the current watermark on, and the wizard's history step fetches older documents.
    Sources under other names belong to PUT /config/sources and are left alone."""
    existing = {s["name"]: s for s in sources}
    for spec in specs:
        old = existing.get(spec["name"])
        if old is None:
            insert_source(conn, connection_id, spec)
        elif list(old["index_patterns"]) != list(spec["index_patterns"]):
            conn.execute("UPDATE source_configs SET index_patterns = %s WHERE id = %s", (list(spec["index_patterns"]), old["id"]))


def forget_connection(conn: psycopg.Connection, store) -> dict | None:
    """DELETE /config/elk: drop the key and the active pointer; synced data and history stay."""
    row = active_connection(conn)
    if row is None:
        return None
    store.delete(row["credential_ref"])
    cancel_queued(conn, [s["id"] for s in list_sources(conn, row["id"])], "Kết nối ELK đã bị xóa")
    delete_value(conn, ACTIVE_CONNECTION)
    return row


def list_sources(conn: psycopg.Connection, connection_id: int, enabled_only: bool = False) -> list[dict]:
    query = f"SELECT {SOURCE_FIELDS} FROM source_configs WHERE connection_id = %s"
    if enabled_only:
        query += " AND enabled"
    return conn.execute(query + " ORDER BY id", (connection_id,)).fetchall()


def get_source(conn: psycopg.Connection, source_id: int) -> dict | None:
    return conn.execute(f"SELECT {SOURCE_FIELDS} FROM source_configs WHERE id = %s", (source_id,)).fetchone()


def source_view(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "index_patterns": list(row["index_patterns"]),
        "timestamp_strategy": row["timestamp_strategy"],
        "timestamp_field": row["timestamp_field"],
        "overlap_s": row["overlap_s"],
        "safety_lag_s": row["safety_lag_s"],
        "batch_size": row["batch_size"],
        "enabled": row["enabled"],
        "state": row["state"],
    }


def normalize_spec(spec: dict) -> dict:
    return {**spec, **{k: v for k, v in SOURCE_DEFAULTS.items() if spec.get(k) is None}}


def insert_source(conn: psycopg.Connection, connection_id: int, spec: dict) -> dict:
    spec = normalize_spec(spec)
    return conn.execute(
        f"""
        INSERT INTO source_configs (connection_id, name, kind, index_patterns, timestamp_strategy, timestamp_field,
                                    overlap_s, safety_lag_s, batch_size, enabled)
        VALUES (%(cid)s, %(name)s, %(kind)s, %(index_patterns)s, %(timestamp_strategy)s, %(timestamp_field)s,
                %(overlap_s)s, %(safety_lag_s)s, %(batch_size)s, %(enabled)s)
        RETURNING {SOURCE_FIELDS}
        """,
        {"cid": connection_id, **spec},
    ).fetchone()


def replace_sources(conn: psycopg.Connection, connection_id: int, specs: list[dict]) -> list[dict]:
    """PUT /config/sources: upsert by name. Sources left out are disabled, not deleted, because synced events reference them."""
    names = [s["name"] for s in specs]
    if len(set(names)) != len(names):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "Tên source bị trùng", {"names": names})
    existing = {s["name"]: s for s in list_sources(conn, connection_id)}
    for spec in map(normalize_spec, specs):
        old = existing.get(spec["name"])
        if old is None:
            insert_source(conn, connection_id, spec)
            continue
        conn.execute(
            """
            UPDATE source_configs SET kind = %(kind)s, index_patterns = %(index_patterns)s, timestamp_strategy = %(timestamp_strategy)s,
                   timestamp_field = %(timestamp_field)s, overlap_s = %(overlap_s)s, safety_lag_s = %(safety_lag_s)s,
                   batch_size = %(batch_size)s, enabled = %(enabled)s
            WHERE id = %(id)s
            """,
            {**spec, "id": old["id"]},
        )
        if old["timestamp_field"] != spec["timestamp_field"]:
            conn.execute("UPDATE sync_checkpoints SET ingest_watermark = NULL, updated_at = now() WHERE source_id = %s", (old["id"],))
        delete_value(conn, streams_key(old["id"]))
        if old["state"] in (SourceState.FORBIDDEN, SourceState.ERROR):
            set_source_state(conn, old["id"], SourceState.ACTIVE)
        if not spec["enabled"]:
            cancel_queued(conn, [old["id"]], "Source đã bị tắt")
    for name, old in existing.items():
        if name not in names and old["enabled"]:
            conn.execute("UPDATE source_configs SET enabled = false WHERE id = %s", (old["id"],))
            cancel_queued(conn, [old["id"]], "Source đã bị tắt")
    return list_sources(conn, connection_id)


def set_source_state(conn: psycopg.Connection, source_id: int, state: SourceState) -> None:
    conn.execute("UPDATE source_configs SET state = %s WHERE id = %s", (state.value, source_id))
    if state != SourceState.ACTIVE:
        cancel_queued(conn, [source_id], f"Source chuyển sang {state.value}")


def cancel_queued(conn: psycopg.Connection, source_ids: list[int], reason: str) -> None:
    if source_ids:
        conn.execute(
            "UPDATE sync_runs SET status = 'CANCELLED', finished_at = now(), error = %s WHERE source_id = ANY(%s) AND status = 'QUEUED'",
            (reason, source_ids),
        )


def ensure_checkpoint(conn: psycopg.Connection, source_id: int) -> dict:
    conn.execute("INSERT INTO sync_checkpoints (source_id) VALUES (%s) ON CONFLICT DO NOTHING", (source_id,))
    return conn.execute("SELECT * FROM sync_checkpoints WHERE source_id = %s", (source_id,)).fetchone()
