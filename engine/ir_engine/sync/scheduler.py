import psycopg

from ..runtime.services import BackgroundService

ACTIVE_SOURCES = """
SELECT s.id, s.name, s.kind, s.index_patterns, s.timestamp_strategy, s.timestamp_field,
       s.overlap_s, s.safety_lag_s, s.batch_size, c.id AS connection_id, c.url, c.cluster_key,
       c.credential_ref, c.ca_pem, c.ca_fingerprint
FROM source_configs s
JOIN elk_connections c ON c.id = s.connection_id
WHERE s.enabled AND s.state = 'ACTIVE' AND c.state = 'ACTIVE'
ORDER BY s.id
"""


def sync_source(conn: psycopg.Connection, source: dict) -> None:
    raise NotImplementedError


class SyncScheduler(BackgroundService):
    name = "sync"

    def __init__(self, db, interval_s: float = 300.0):
        super().__init__(db)
        self.interval_s = interval_s

    def tick(self) -> bool:
        with self.db.connection() as conn:
            sources = conn.execute(ACTIVE_SOURCES).fetchall()
        for source in sources:
            with self.db.connection() as conn:
                sync_source(conn, source)
        return False
