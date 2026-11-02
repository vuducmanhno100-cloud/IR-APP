import psycopg

from ..runtime.services import BackgroundService

PARSER_VERSION = 1
RULE_VERSION = "sysmon-eid1-v1"


def run_once(conn: psycopg.Connection, batch_size: int = 500) -> int:
    raise NotImplementedError


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
