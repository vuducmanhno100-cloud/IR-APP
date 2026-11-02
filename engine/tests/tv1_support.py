import copy
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from ir_engine.app import create_app
from ir_engine.credentials import MemoryStore
from ir_engine.db import Database
from ir_engine.elk.client import make_client
from ir_engine.routers import tv1
from ir_engine.runtime.services import ServiceManager
from ir_engine.sync.scheduler import SyncScheduler
from ir_engine.testing.contract import EngineClient

ES_URL = "https://elk.lab.test:9200"
FINGERPRINT = ":".join(["AB"] * 32)
API_KEY = "fake-api-key"
NOW = datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc)
DAY_START = datetime(2026, 10, 30, 0, 0, tzinfo=timezone.utc)
LOG_DS_INDEX = ".ds-logs-windows.sysmon_operational-default-2026.10.30-000001"


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> datetime:
        self.now += timedelta(**delta)
        return self.now


def fake_factory(fake):
    """Stands in for make_client: same arguments, but talks HTTP to the fake instead of TLS to a real cluster."""

    def factory(url, api_key, ca_pem=None, ca_fingerprint=None, **_):
        assert url == ES_URL and (ca_pem or ca_fingerprint)
        return make_client(fake.url, api_key, max_retries=0)

    return factory


def connection_input(**overrides) -> dict:
    return {"url": ES_URL, "api_key": API_KEY, "ca_fingerprint": FINGERPRINT, **overrides}


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def log_hit(fake, record_id: int, ingested: datetime, namespace: str = "default") -> dict:
    """A copy of a fixture EID 1 with new identity, as if Elastic Agent shipped a new event."""
    base = next(d for d in fake.state.docs if d["_index"] == LOG_DS_INDEX and d["_source"]["event"]["code"] == "1")
    hit = copy.deepcopy(base)
    src = hit["_source"]
    src["winlog"]["record_id"] = str(record_id)
    src["event"]["ingested"] = iso(ingested)
    src["@timestamp"] = iso(ingested - timedelta(seconds=2))
    src["data_stream"]["namespace"] = namespace
    hit["_id"] = f"new-{namespace}-{record_id}"
    hit["_index"] = f".ds-logs-windows.sysmon_operational-{namespace}-2026.10.30-000001"
    return hit


class Harness:
    """Engine API + sync scheduler sharing one database, a memory key store, the fake cluster and a fixed clock."""

    def __init__(self, dsn: str, fake, clock: Clock):
        self.fake = fake
        self.clock = clock
        self.store = MemoryStore()
        self.db = Database(dsn)
        self.db.open()
        self.scheduler = SyncScheduler(self.db, store=self.store, client_factory=fake_factory(fake), clock=clock)
        app = create_app(secrets.token_urlsafe(32), self.db, ServiceManager([self.scheduler]))
        app.dependency_overrides[tv1.secret_store] = lambda: self.store
        app.dependency_overrides[tv1.client_factory] = lambda: fake_factory(fake)
        app.dependency_overrides[tv1.clock] = clock
        self.api = EngineClient(app)
        self.dsn = dsn

    def close(self) -> None:
        self.db.close()

    def configure(self, **overrides) -> dict:
        response = self.api.put("/config/elk", json=connection_input(**overrides))
        assert response.status_code == 200, response.text
        return response.json()

    def source_id(self, name: str) -> int:
        return self.one("SELECT id FROM source_configs WHERE name = %s ORDER BY id DESC LIMIT 1", (name,))["id"]

    def tick_until_idle(self, limit: int = 60) -> int:
        for n in range(1, limit + 1):
            if not self.scheduler.tick():
                return n
        raise AssertionError("scheduler never became idle")

    def rows(self, query: str, params=()) -> list[dict]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            return conn.execute(query, params).fetchall()

    def one(self, query: str, params=()) -> dict | None:
        rows = self.rows(query, params)
        return rows[0] if rows else None

    def execute(self, query: str, params=()) -> None:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(query, params)

    def runs(self, source_id: int | None = None) -> list[dict]:
        if source_id is None:
            return self.rows("SELECT * FROM sync_runs ORDER BY id")
        return self.rows("SELECT * FROM sync_runs WHERE source_id = %s ORDER BY id", (source_id,))

    def checkpoint(self, source_id: int) -> dict:
        return self.one("SELECT * FROM sync_checkpoints WHERE source_id = %s", (source_id,))
