import logging
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
from elastic_transport import TransportError
from elasticsearch import ApiError as EsApiError
from elasticsearch import Elasticsearch

from ..audit import ACTOR_SYSTEM, record
from ..contracts import ConnectionState, ErrorCode, RunStatus, RunType, SourceKind, SourceState
from ..credentials import shared_store
from ..elk import queries
from ..elk.client import make_client
from ..runtime.services import BackgroundService
from ..storage import retention
from ..storage.usage import record_sample, refresh_reusable, reusable_due, usage
from . import access, config, runner
from .common import utcnow

log = logging.getLogger("ir_engine.sync")

INCREMENTAL_SPAN_S = 3600
ALERT_STATUS_SPAN_S = 86400
BOOTSTRAP_CHUNK_S = {SourceKind.LOG: 3600, SourceKind.ALERT: 86400}
RECONCILE_EVERY_S = 3600
RETENTION_EVERY_S = 3600
RETENTION_PRESSURE_EVERY_S = 600
BACKOFF_BASE_S = 30
BACKOFF_MAX_S = 900
BACKOFF_CODES = {ErrorCode.ELK_UNREACHABLE, ErrorCode.ELK_RATE_LIMITED, ErrorCode.INTERNAL_ERROR, ErrorCode.SYNC_PARTIAL}
BLOCKED_CONNECTION = {ConnectionState.AUTH_FAILED, ConnectionState.TLS_ERROR}


@dataclass
class SyncContext:
    es: Elasticsearch
    connection: dict
    settings: dict
    now: datetime
    clock: Callable[[], datetime]
    should_stop: Callable[[], bool]

    @property
    def interval(self) -> timedelta:
        return timedelta(seconds=self.settings["sync_interval_s"])

    @property
    def blocked(self) -> bool:
        return self.connection["state"] in BLOCKED_CONNECTION


def chunk_span(source: dict) -> int:
    return BOOTSTRAP_CHUNK_S[SourceKind(source["kind"])]


def initial_watermark(source: dict, settings: dict, now: datetime) -> datetime:
    """Without a bootstrap, start with the last sync interval; history comes from POST /sync/bootstrap."""
    return now - timedelta(seconds=source["safety_lag_s"] + settings["sync_interval_s"])


def prepare_checkpoint(conn: psycopg.Connection, source: dict, settings: dict, now: datetime) -> dict:
    cp = config.ensure_checkpoint(conn, source["id"])
    if cp["ingest_watermark"] is None:
        conn.execute(
            "UPDATE sync_checkpoints SET ingest_watermark = %s WHERE source_id = %s",
            (initial_watermark(source, settings, now), source["id"]),
        )
    if source["kind"] == SourceKind.ALERT and cp["alert_status_watermark"] is None:
        start = now - timedelta(days=settings["alert_resync_days"])
        conn.execute("UPDATE sync_checkpoints SET alert_status_watermark = %s WHERE source_id = %s", (start, source["id"]))
    conn.commit()
    return config.ensure_checkpoint(conn, source["id"])


def in_backoff(conn: psycopg.Connection, source_id: int, now: datetime) -> bool:
    """Exponential backoff after transient failures (30s, 60s, ... 15 min), computed from run history so it survives restarts."""
    recent = runner.last_finished(conn, source_id, limit=8)
    failures = 0
    for run in recent:
        if run["status"] in (RunStatus.FAILED, RunStatus.PARTIAL) and run["error_code"] in BACKOFF_CODES:
            failures += 1
        else:
            break
    if not failures:
        return False
    delay = min(BACKOFF_BASE_S * 2 ** (failures - 1), BACKOFF_MAX_S)
    return now < recent[0]["finished_at"] + timedelta(seconds=delay)


def is_due(conn: psycopg.Connection, source_id: int, run_type: RunType, now: datetime, every: timedelta) -> bool:
    last = runner.last_started(conn, source_id, run_type)
    return last is None or now - last >= every - timedelta(seconds=1)


def behind(watermark: datetime, ctx: "SyncContext", lag: timedelta) -> bool:
    """A backlog (app was closed, or a window was capped at max span) is worked off without waiting for the interval."""
    return watermark < ctx.now - lag - ctx.interval


def reconcile_window(
    source: dict, cp: dict, settings: dict, now: datetime, synced_from: datetime | None
) -> tuple[datetime, datetime] | None:
    """Only the period already synced: never before the first successful incremental or bootstrap run (history the
    user did not ask for is bootstrap's job; on a first connection this used to pull a whole day and keep bootstrap
    busy), never past local retention, and never ahead of what incremental has covered."""
    if synced_from is None:
        return None
    lower = max(now - timedelta(hours=settings["reconcile_window_h"]), retention.cutoff(now, settings["retention_days"]), synced_from)
    upper = min(now - timedelta(seconds=source["safety_lag_s"]), cp["ingest_watermark"])
    return (lower, upper) if lower < upper else None


def set_source_state(conn: psycopg.Connection, source: dict, state: SourceState, reason: str) -> None:
    if source["state"] == state:
        return
    config.set_source_state(conn, source["id"], state)
    record(
        conn,
        "sync.source.state",
        f"source_configs:{source['id']}",
        {"from": source["state"], "to": state.value, "reason": reason},
        ACTOR_SYSTEM,
    )
    source["state"] = state.value


def block_connection(
    conn: psycopg.Connection, ctx_connection: dict, state: ConnectionState, source_state: SourceState, reason: str
) -> None:
    config.set_connection_state(conn, ctx_connection["id"], state)
    record(conn, "sync.connection.state", f"elk_connections:{ctx_connection['id']}", {"to": state.value, "reason": reason}, ACTOR_SYSTEM)
    ctx_connection["state"] = state.value
    for source in config.list_sources(conn, ctx_connection["id"]):
        set_source_state(conn, dict(source), source_state, reason)


def apply_outcome(conn: psycopg.Connection, ctx: SyncContext, source: dict, outcome: runner.Outcome) -> None:
    code = outcome.error_code
    if outcome.ok:
        if ctx.connection["state"] == ConnectionState.UNREACHABLE:
            config.set_connection_state(conn, ctx.connection["id"], ConnectionState.ACTIVE)
            ctx.connection["state"] = ConnectionState.ACTIVE.value
    elif outcome.status == RunStatus.FAILED:
        if code == ErrorCode.ELK_AUTH_FAILED:
            block_connection(conn, ctx.connection, ConnectionState.AUTH_FAILED, SourceState.AUTH_FAILED, code.value)
        elif code == ErrorCode.ELK_TLS_ERROR:
            block_connection(conn, ctx.connection, ConnectionState.TLS_ERROR, SourceState.ERROR, code.value)
        elif code == ErrorCode.ELK_FORBIDDEN:
            set_source_state(conn, source, SourceState.FORBIDDEN, code.value)
        elif code == ErrorCode.ELK_BAD_REQUEST:
            set_source_state(conn, source, SourceState.ERROR, code.value)
        elif code == ErrorCode.ELK_UNREACHABLE:
            config.set_connection_state(conn, ctx.connection["id"], ConnectionState.UNREACHABLE)
            ctx.connection["state"] = ConnectionState.UNREACHABLE.value
    conn.commit()


def run_window(
    conn: psycopg.Connection,
    ctx: SyncContext,
    source: dict,
    run_type: RunType,
    window: tuple[datetime, datetime],
    check_access: bool = False,
) -> runner.Outcome | None:
    try:
        run = runner.create_run(conn, source["id"], run_type, *window)
    except runner.RunBusy:
        return None
    conn.commit()
    return execute(conn, ctx, source, run, check_access)


def execute(conn: psycopg.Connection, ctx: SyncContext, source: dict, run: dict, check_access: bool = False) -> runner.Outcome:
    precheck = (lambda: access.check(conn, ctx.es, source)) if check_access else None
    outcome = runner.execute_run(conn, ctx.es, source, ctx.connection["cluster_key"], run, ctx.clock, ctx.should_stop, precheck)
    apply_outcome(conn, ctx, source, outcome)
    return outcome


def sync_source(conn: psycopg.Connection, ctx: SyncContext, source: dict) -> bool:
    """One scheduler pass for one source. Returns True when more work is pending right away (backlog or bootstrap)."""
    source = dict(source)
    if source["state"] == SourceState.FORBIDDEN and access.recheck_forbidden(ctx.es, source):
        set_source_state(conn, source, SourceState.ACTIVE, "privileges restored")
        conn.commit()
    if source["state"] != SourceState.ACTIVE:
        return False
    queued = runner.queued_run(conn, source["id"])
    if queued:
        return execute(conn, ctx, source, queued, check_access=queued["run_type"] == RunType.INCREMENTAL).ok
    if in_backoff(conn, source["id"], ctx.now):
        return False
    cp = prepare_checkpoint(conn, source, ctx.settings, ctx.now)
    lag = timedelta(seconds=source["safety_lag_s"])
    more = False

    if is_due(conn, source["id"], RunType.INCREMENTAL, ctx.now, ctx.interval) or behind(cp["ingest_watermark"], ctx, lag):
        window = queries.next_window(cp["ingest_watermark"], ctx.now, source["overlap_s"], source["safety_lag_s"], INCREMENTAL_SPAN_S)
        if window:
            outcome = run_window(conn, ctx, source, RunType.INCREMENTAL, window, check_access=True)
            if outcome is None or not outcome.ok:
                return False
            more = window[1] < ctx.now - lag
    if source["kind"] == SourceKind.ALERT and (
        is_due(conn, source["id"], RunType.ALERT_RESYNC, ctx.now, ctx.interval) or behind(cp["alert_status_watermark"], ctx, lag)
    ):
        window = queries.next_window(
            cp["alert_status_watermark"], ctx.now, source["overlap_s"], source["safety_lag_s"], ALERT_STATUS_SPAN_S
        )
        if window:
            outcome = run_window(conn, ctx, source, RunType.ALERT_RESYNC, window)
            if outcome is None or not outcome.ok:
                return False
            more = more or window[1] < ctx.now - lag

    plan = config.get_plan(conn, source["id"])
    if plan and not ctx.should_stop():
        upper = min(plan["next"] + timedelta(seconds=chunk_span(source)), plan["to"])
        outcome = run_window(conn, ctx, source, RunType.BOOTSTRAP, (plan["next"], upper))
        if outcome is None or not outcome.ok:
            return False
        if config.get_plan(conn, source["id"]) is not None:
            return True

    if not ctx.should_stop() and is_due(conn, source["id"], RunType.RECONCILE, ctx.now, timedelta(seconds=RECONCILE_EVERY_S)):
        window = reconcile_window(
            source, config.ensure_checkpoint(conn, source["id"]), ctx.settings, ctx.now, runner.synced_since(conn, source["id"])
        )
        if window:
            run_window(conn, ctx, source, RunType.RECONCILE, window)
    return more


class SyncScheduler(BackgroundService):
    name = "sync"

    def __init__(
        self,
        db,
        interval_s: float = 300.0,
        store=None,
        client_factory: Callable[..., Elasticsearch] = make_client,
        clock: Callable[[], datetime] = utcnow,
        data_dir: Path | None = None,
    ):
        super().__init__(db)
        self.interval_s = interval_s
        self.store = store if store is not None else shared_store()
        self.client_factory = client_factory
        self.clock = clock
        self.data_dir = data_dir
        self._recovered = False
        self._last_retention: datetime | None = None
        self._unvacuumed = 0

    def start(self) -> None:
        try:
            with self.db.connection() as conn:
                paused = bool(config.get_value(conn, config.SYNC_PAUSED, False))
        except psycopg.Error:
            paused = False
        if paused:
            self.pause()
        super().start()

    def tick(self) -> bool:
        now = self.clock()
        with self.db.connection() as conn:
            if not self._recovered:
                runner.recover_interrupted(conn, now)
                conn.commit()
                self._recovered = True
            settings = config.get_settings(conn)
            self.interval_s = float(settings["sync_interval_s"])
            threshold = self.maintain_storage(conn, settings, now)
            connection = config.active_connection(conn)
            if connection is None or connection["state"] in BLOCKED_CONNECTION:
                conn.commit()
                return False
            connection = dict(connection)
            sources = [dict(s) for s in config.list_sources(conn, connection["id"], enabled_only=True)]
            if not self.apply_quota(conn, sources, threshold):
                return False
            es = self.open_client(conn, connection)
            if es is None:
                return False
            ctx = SyncContext(es, connection, settings, now, self.clock, self.should_interrupt)
            try:
                return self.sync_all(conn, ctx, sources)
            finally:
                es.close()

    def sync_all(self, conn: psycopg.Connection, ctx: SyncContext, sources: list[dict]) -> bool:
        more = False
        for source in sources:
            if self.should_interrupt() or ctx.blocked:
                break
            try:
                more = sync_source(conn, ctx, source) or more
            except (EsApiError, TransportError) as exc:
                conn.rollback()
                log.warning("sync pass for source %s failed: %s", source["id"], type(exc).__name__)
            except Exception:
                conn.rollback()
                log.exception("sync pass for source %s failed", source["id"])
        conn.commit()
        return more and not self.should_interrupt()

    def open_client(self, conn: psycopg.Connection, connection: dict) -> Elasticsearch | None:
        api_key = self.store.get(connection["credential_ref"])
        if not api_key:
            block_connection(
                conn, connection, ConnectionState.AUTH_FAILED, SourceState.AUTH_FAILED, "api key missing from credential store"
            )
            conn.commit()
            return None
        try:
            return self.client_factory(connection["url"], api_key, ca_pem=connection["ca_pem"], ca_fingerprint=connection["ca_fingerprint"])
        except (ssl.SSLError, ValueError):
            block_connection(conn, connection, ConnectionState.TLS_ERROR, SourceState.ERROR, "stored CA is unusable")
            conn.commit()
            return None

    def maintain_storage(self, conn: psycopg.Connection, settings: dict, now: datetime) -> str:
        """Hourly retention; every 10 minutes while usage is at or above 80%. Deleted rows lower usage only once
        VACUUM has made their space reusable, so a large delete, or any delete while blocked, is vacuumed at once."""
        if reusable_due(conn, now):
            refresh_reusable(conn, now)
        current = usage(conn, self.data_dir, settings["quota_bytes"])
        state = current["threshold_state"]
        every = RETENTION_PRESSURE_EVERY_S if state in ("WARN_80", "BLOCK_90") else RETENTION_EVERY_S
        if self._last_retention is None or now - self._last_retention >= timedelta(seconds=every):
            deleted = retention.apply(conn, now, settings["retention_days"])
            self._last_retention = now
            if deleted:
                log.info("retention removed %s events", deleted)
                self._unvacuumed += deleted
            if retention.vacuum_due(conn, self._unvacuumed, blocked=state == "BLOCK_90"):
                self.reclaim(conn, now)
            current = usage(conn, self.data_dir, settings["quota_bytes"])
        record_sample(conn, now, current["used_bytes"])
        conn.commit()
        return current["threshold_state"]

    def reclaim(self, conn: psycopg.Connection, now: datetime) -> None:
        try:
            retention.reclaim(conn)
            refresh_reusable(conn, now)
            self._unvacuumed = 0
        except psycopg.Error:
            conn.rollback()
            log.exception("VACUUM after retention failed; the freed space counts as used until autovacuum runs")

    def apply_quota(self, conn: psycopg.Connection, sources: list[dict], threshold: str) -> bool:
        blocked = threshold == "BLOCK_90"
        for source in sources:
            if blocked and source["state"] == SourceState.ACTIVE:
                set_source_state(conn, source, SourceState.QUOTA_BLOCKED, ErrorCode.STORAGE_QUOTA.value)
            elif not blocked and source["state"] == SourceState.QUOTA_BLOCKED:
                set_source_state(conn, source, SourceState.ACTIVE, "usage below 90%")
        conn.commit()
        return not blocked
