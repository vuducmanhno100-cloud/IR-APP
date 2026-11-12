import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
from psycopg import sql

from ..sync import config
from ..sync.common import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

THRESHOLDS = ((90, "BLOCK_90"), (80, "WARN_80"), (70, "WARN_70"))
SAMPLES_KEY = "usage_samples"
SAMPLE_EVERY = timedelta(hours=1)
MIN_SPAN = timedelta(hours=1)
KEEP_SAMPLES = 48

REUSABLE_KEY = "usage_reusable"
REUSABLE_EVERY = timedelta(minutes=10)
FREESPACE_EXTENSION = "pg_freespacemap"
# Tables, TOAST and materialized views, counting only pages at least half free: retention empties whole pages,
# while the few hundred bytes left at the end of every page after inserts rarely fit another event. Index pages
# emptied by DELETE are recycled only after later VACUUMs, so they are left out too.
REUSABLE_SQL = """
SELECT coalesce(sum(f.avail), 0) AS n
FROM pg_class c
JOIN pg_namespace s ON s.oid = c.relnamespace
CROSS JOIN LATERAL pg_freespace(c.oid) f
WHERE c.relkind IN ('r', 't', 'm') AND s.nspname NOT IN ('pg_catalog', 'information_schema')
  AND f.avail >= current_setting('block_size')::int / 2
"""
_unavailable_logged = False


def dir_size(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _optional_size(conn: psycopg.Connection, query: str) -> int:
    """WAL and temp listings need superuser or pg_monitor; the embedded cluster has it, a dev database may not."""
    try:
        with conn.transaction():
            row = conn.execute(query).fetchone()
        return int(row["n"] or 0)
    except psycopg.Error:
        return 0


def threshold_state(percent: float) -> str:
    return next((state for limit, state in THRESHOLDS if percent >= limit), "OK")


def samples(conn: psycopg.Connection) -> list[tuple[datetime, int]]:
    return [(parse_iso(at), int(n)) for at, n in config.get_value(conn, SAMPLES_KEY, [])]


def record_sample(conn: psycopg.Connection, now: datetime, used_bytes: int) -> None:
    """Hourly footprint samples (the scheduler calls this every pass); growth is read from them in O(1)."""
    known = samples(conn)
    if known and now - known[-1][0] < SAMPLE_EVERY:
        return
    known.append((now, used_bytes))
    config.set_value(conn, SAMPLES_KEY, [[iso(at), n] for at, n in known[-KEEP_SAMPLES:]])


def growth_per_day(conn: psycopg.Connection, now: datetime, used_bytes: int) -> int | None:
    """Bytes per day from the sample closest to 24 hours ago (at least 1 hour old). Counting raw_events instead
    would scan the whole table on every call (about 0.3 s per million events)."""
    usable = [(at, n) for at, n in samples(conn) if now - at >= MIN_SPAN]
    if not usable:
        return None
    at, n = min(usable, key=lambda s: abs((now - s[0]) - timedelta(days=1)))
    return max(int((used_bytes - n) / ((now - at) / timedelta(days=1))), 0)


def measure_reusable(conn: psycopg.Connection) -> int | None:
    """Bytes inside the database files that PostgreSQL fills before it grows them. DELETE never shrinks a file;
    after VACUUM the space of deleted rows is in the free space map and new rows go there first.
    None when pg_freespacemap cannot be used (a dev database without superuser); the embedded cluster can."""
    global _unavailable_logged
    try:
        with conn.transaction():
            conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(FREESPACE_EXTENSION)))
            return int(conn.execute(REUSABLE_SQL).fetchone()["n"])
    except psycopg.Error as exc:
        if not _unavailable_logged:
            log.warning("free space map unavailable (%s); space freed by retention counts as used", type(exc).__name__)
            _unavailable_logged = True
        return None


def refresh_reusable(conn: psycopg.Connection, now: datetime) -> int:
    """Reads the whole free space map (about 1 s per 10 GB), so the scheduler refreshes it every 10 minutes and after
    VACUUM; /storage/usage reads the stored value. Until the next refresh new rows may already use part of it,
    which understates usage by at most 10 minutes of ingest."""
    measured = measure_reusable(conn) or 0
    config.set_value(conn, REUSABLE_KEY, {"bytes": measured, "at": iso(now)})
    return measured


def reusable_due(conn: psycopg.Connection, now: datetime) -> bool:
    stored = config.get_value(conn, REUSABLE_KEY)
    return stored is None or now - parse_iso(stored["at"]) >= REUSABLE_EVERY


def stored_reusable(conn: psycopg.Connection) -> int:
    stored = config.get_value(conn, REUSABLE_KEY)
    return int(stored["bytes"]) if stored else 0


def usage(
    conn: psycopg.Connection, data_dir: Path | None, quota_bytes: int, with_growth: bool = False, now: datetime | None = None
) -> dict:
    """`database` is what the data occupies: the database files minus the free space PostgreSQL will reuse.
    Counting whole files would keep usage at its peak after retention, and a source blocked at 90% would never resume."""
    allocated = int(conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"])
    database = allocated - min(stored_reusable(conn), allocated)
    wal = _optional_size(conn, "SELECT coalesce(sum(size), 0) AS n FROM pg_ls_waldir()")
    temp = _optional_size(conn, "SELECT coalesce(sum(size), 0) AS n FROM pg_ls_tmpdir()")
    exports = dir_size(data_dir / "exports") if data_dir else 0
    used = database + wal + temp + exports
    percent = round(used * 100 / quota_bytes, 2) if quota_bytes else 100.0
    return {
        "quota_bytes": quota_bytes,
        "used_bytes": used,
        "percent": percent,
        "threshold_state": threshold_state(percent),
        "growth_bytes_per_day": growth_per_day(conn, now or utcnow(), used) if with_growth else None,
        "breakdown": {"database": database, "wal": wal, "exports": exports, "temp": temp},
    }
