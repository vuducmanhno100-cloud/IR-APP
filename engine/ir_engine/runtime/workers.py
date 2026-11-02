from pathlib import Path

from ..correlation.worker import CorrelationWorker
from ..db import Database
from ..sync.scheduler import SyncScheduler
from .services import ServiceManager

WORKER_CHOICES = ("all", "none", "sync", "correlation")


def _sync_interval(db: Database) -> float:
    with db.connection() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'sync_interval_s'").fetchone()
    return float(row["value"]) if row else 300.0


def build_services(db: Database, selection: str = "all", store=None, data_dir: Path | None = None) -> ServiceManager:
    services = []
    if selection in ("all", "sync"):
        services.append(SyncScheduler(db, interval_s=_sync_interval(db), store=store, data_dir=data_dir))
    if selection in ("all", "correlation"):
        services.append(CorrelationWorker(db))
    return ServiceManager(services)
