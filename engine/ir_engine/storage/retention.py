from datetime import datetime, timedelta

import psycopg

BATCH_SIZE = 5000
VACUUM_FRACTION = 0.01

# TV1 deletes only raw_events; TV2's tables follow through ON DELETE CASCADE / SET NULL (migration 0002).
# Events pinned to a case are kept regardless of age.
DELETE_BATCH = """
DELETE FROM raw_events WHERE id IN (
    SELECT r.id FROM raw_events r
    WHERE r.event_time < %(cutoff)s
      AND NOT EXISTS (SELECT 1 FROM case_items c WHERE c.item_type = 'event' AND c.ref_id = r.id)
    LIMIT %(batch)s
)
"""


def cutoff(now: datetime, retention_days: int) -> datetime:
    return now - timedelta(days=retention_days)


def apply(conn: psycopg.Connection, now: datetime, retention_days: int, batch_size: int = BATCH_SIZE) -> int:
    """Delete expired events in small committed batches so the analyst is never blocked by one long transaction."""
    limit = cutoff(now, retention_days)
    conn.commit()
    deleted = 0
    while True:
        n = conn.execute(DELETE_BATCH, {"cutoff": limit, "batch": batch_size}).rowcount
        conn.commit()
        deleted += n
        if n < batch_size:
            break
    conn.execute("DELETE FROM sync_runs WHERE finished_at < %s", (limit,))
    conn.commit()
    return deleted


def vacuum_due(conn: psycopg.Connection, deleted_since_vacuum: int, blocked: bool) -> bool:
    """Autovacuum waits until a fifth of a table is dead, while retention removes about 1/retention_days of the events
    a day, so freed space would stay unusable for weeks. A source blocked at 90% only resumes once it is reusable."""
    if deleted_since_vacuum <= 0:
        return False
    if blocked:
        return True
    rows = conn.execute("SELECT greatest(reltuples, 0)::bigint AS n FROM pg_class WHERE oid = 'raw_events'::regclass").fetchone()["n"]
    return deleted_since_vacuum >= rows * VACUUM_FRACTION


def reclaim(conn: psycopg.Connection) -> None:
    """Plain VACUUM makes the space of deleted rows reusable and lists it in the free space map. It does not block
    reads or writes and only gives back empty pages at the end of a file; VACUUM FULL would lock every table and
    need as much free disk again as the data it rewrites."""
    conn.commit()
    conn.autocommit = True
    try:
        conn.execute("VACUUM (ANALYZE)")
    finally:
        conn.autocommit = False
