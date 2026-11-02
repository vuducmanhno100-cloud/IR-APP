from datetime import datetime, timedelta

import psycopg

BATCH_SIZE = 5000

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
