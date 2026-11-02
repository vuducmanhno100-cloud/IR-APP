import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg
from elastic_transport import TransportError
from elasticsearch import ApiError as EsApiError
from elasticsearch import Elasticsearch

from ..contracts import ErrorCode, RunStatus, RunType, SourceKind
from ..elk import queries
from ..elk.errors import classify_exception
from ..ingest.writer import SourceRef, WriteStats, write_hits
from ..logs import redact
from . import config
from .common import iso

log = logging.getLogger("ir_engine.sync")

RUN_FIELDS = (
    "id, source_id, run_type, status, lower_bound, upper_bound, started_at, finished_at, fetched_count, upserted_count, "
    "duplicate_skipped, error_code, error"
)
RETRYABLE_STATUSES = (RunStatus.FAILED, RunStatus.PARTIAL, RunStatus.CANCELLED)
RECONCILE_BUCKET_S = 3600
LOCAL_TIME_COLUMN = {"event.ingested": "ingest_time", "@timestamp": "event_time"}
PARTIAL_MESSAGE = "Elasticsearch trả kết quả không đầy đủ (timeout hoặc shard lỗi); watermark giữ nguyên"
STOPPED_MESSAGE = "Dừng sau batch hiện tại (tạm dừng hoặc tắt ứng dụng)"


class RunBusy(Exception):
    pass


class Interrupted(Exception):
    pass


@dataclass
class Totals:
    fetched: int = 0
    upserted: int = 0
    duplicates: int = 0
    indices: dict = field(default_factory=dict)

    def add(self, stats: WriteStats) -> None:
        self.fetched += stats.fetched
        self.upserted += stats.inserted + stats.updated
        self.duplicates += stats.duplicates
        for name, n in stats.indices.items():
            self.indices[name] = self.indices.get(name, 0) + n


@dataclass(frozen=True)
class Outcome:
    run: dict
    status: RunStatus
    error_code: ErrorCode | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == RunStatus.SUCCEEDED


@dataclass(frozen=True)
class Access:
    denied: str | None = None
    warning: str | None = None


def run_view(row: dict) -> dict:
    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "run_type": row["run_type"],
        "status": row["status"],
        "lower_bound": iso(row["lower_bound"]),
        "upper_bound": iso(row["upper_bound"]),
        "started_at": iso(row["started_at"]),
        "finished_at": iso(row["finished_at"]),
        "fetched_count": row["fetched_count"],
        "upserted_count": row["upserted_count"],
        "duplicate_skipped": row["duplicate_skipped"],
        "error_code": row["error_code"],
        "error": row["error"],
    }


def create_run(conn: psycopg.Connection, source_id: int, run_type: RunType, lower: datetime, upper: datetime) -> dict:
    """Queue a run. The partial unique index ux_sync_runs_active allows one active run per source."""
    try:
        with conn.transaction():
            return conn.execute(
                f"""
                INSERT INTO sync_runs (source_id, run_type, status, lower_bound, upper_bound)
                VALUES (%s, %s, 'QUEUED', %s, %s) RETURNING {RUN_FIELDS}
                """,
                (source_id, run_type.value, lower, upper),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise RunBusy(source_id) from None


def get_run(conn: psycopg.Connection, run_id: int) -> dict | None:
    return conn.execute(f"SELECT {RUN_FIELDS} FROM sync_runs WHERE id = %s", (run_id,)).fetchone()


def active_run(conn: psycopg.Connection, source_id: int) -> dict | None:
    return conn.execute(
        f"SELECT {RUN_FIELDS} FROM sync_runs WHERE source_id = %s AND status IN ('QUEUED','FETCHING','RAW_COMMITTED')",
        (source_id,),
    ).fetchone()


def queued_run(conn: psycopg.Connection, source_id: int) -> dict | None:
    return conn.execute(
        f"SELECT {RUN_FIELDS} FROM sync_runs WHERE source_id = %s AND status = 'QUEUED' ORDER BY id LIMIT 1", (source_id,)
    ).fetchone()


def last_finished(conn: psycopg.Connection, source_id: int, limit: int = 1) -> list[dict]:
    return conn.execute(
        f"SELECT {RUN_FIELDS} FROM sync_runs WHERE source_id = %s AND finished_at IS NOT NULL ORDER BY id DESC LIMIT %s",
        (source_id, limit),
    ).fetchall()


def last_started(conn: psycopg.Connection, source_id: int, run_type: RunType) -> datetime | None:
    row = conn.execute(
        "SELECT max(started_at) AS at FROM sync_runs WHERE source_id = %s AND run_type = %s", (source_id, run_type.value)
    ).fetchone()
    return row["at"]


def recover_interrupted(conn: psycopg.Connection, now: datetime) -> int:
    """A crash leaves runs FETCHING forever and the unique index would then block the source; close them on startup.

    CANCELLED (not FAILED) so the source is not put into backoff: the next pass re-reads the window and dedup absorbs it."""
    return conn.execute(
        """
        UPDATE sync_runs SET status = 'CANCELLED', finished_at = %s,
               error = 'Engine dừng giữa chừng khi run đang chạy; dữ liệu đã ghi được giữ, cửa sổ sẽ được tải lại'
        WHERE status IN ('FETCHING','RAW_COMMITTED')
        """,
        (now,),
    ).rowcount


def buckets(lower: datetime, upper: datetime, span_s: int) -> Iterator[tuple[datetime, datetime]]:
    start = lower
    while start < upper:
        end = min(start + timedelta(seconds=span_s), upper)
        yield start, end
        start = end


def _update(conn: psycopg.Connection, run_id: int, **values) -> dict:
    assignments = ", ".join(f"{name} = %({name})s" for name in values)
    return conn.execute(f"UPDATE sync_runs SET {assignments} WHERE id = %(id)s RETURNING {RUN_FIELDS}", {**values, "id": run_id}).fetchone()


def _save_progress(conn: psycopg.Connection, run_id: int, totals: Totals) -> None:
    _update(conn, run_id, fetched_count=totals.fetched, upserted_count=totals.upserted, duplicate_skipped=totals.duplicates)


def _save_indices(conn: psycopg.Connection, run_id: int, totals: Totals) -> None:
    for name, n in totals.indices.items():
        conn.execute(
            """
            INSERT INTO sync_run_indices (run_id, index_name, doc_count) VALUES (%s, %s, %s)
            ON CONFLICT (run_id, index_name) DO UPDATE SET doc_count = EXCLUDED.doc_count
            """,
            (run_id, name, n),
        )


def _finish(
    conn: psycopg.Connection, run: dict, totals: Totals, status: RunStatus, at: datetime, code: ErrorCode | None, message: str | None
) -> Outcome:
    row = _update(
        conn,
        run["id"],
        status=status.value,
        finished_at=at,
        error_code=code.value if code else None,
        error=message[:2000] if message else None,
        fetched_count=totals.fetched,
        upserted_count=totals.upserted,
        duplicate_skipped=totals.duplicates,
    )
    _save_indices(conn, run["id"], totals)
    conn.commit()
    return Outcome(row, status, code, message)


def _close_pit(es: Elasticsearch, pit_id: str | None) -> None:
    if not pit_id:
        return
    try:
        es.close_point_in_time(id=pit_id)
    except (EsApiError, TransportError):
        log.warning("could not close point in time")


def _fetch(
    conn: psycopg.Connection,
    es: Elasticsearch,
    ref: SourceRef,
    source: dict,
    field_name: str,
    lower: datetime,
    upper: datetime,
    run_id: int,
    totals: Totals,
    should_stop: Callable[[], bool],
) -> bool:
    """Read [lower, upper) with PIT + search_after. Each batch is committed before the next page is requested,
    so a crash loses at most one batch and dedup absorbs the re-read. Returns False on partial results."""
    pit = es.open_point_in_time(index=",".join(source["index_patterns"]), **queries.open_pit_params())["id"]
    size = source["batch_size"]
    after = None
    try:
        while True:
            if should_stop():
                raise Interrupted
            resp = es.search(**queries.window_search(pit, field_name, lower, upper, size, after)).body
            if queries.is_partial(resp):
                return False
            pit = resp.get("pit_id", pit)
            hits = resp["hits"]["hits"]
            if not hits:
                return True
            conn.commit()
            totals.add(write_hits(conn, ref, hits))
            _save_progress(conn, run_id, totals)
            conn.commit()
            if len(hits) < size:
                return True
            after = hits[-1]["sort"]
    finally:
        _close_pit(es, pit)


def _local_counts(conn: psycopg.Connection, source_id: int, column: str, lower: datetime, upper: datetime) -> dict:
    rows = conn.execute(
        f"""
        SELECT date_bin(make_interval(secs => %(span)s), {column}, %(lower)s) AS bucket, count(*) AS n
        FROM raw_events
        WHERE source_id = %(sid)s AND {column} >= %(lower)s AND {column} < %(upper)s
        GROUP BY 1
        """,
        {"span": RECONCILE_BUCKET_S, "sid": source_id, "lower": lower, "upper": upper},
    ).fetchall()
    return {r["bucket"]: r["n"] for r in rows}


def _reconcile(
    conn: psycopg.Connection, es: Elasticsearch, ref: SourceRef, source: dict, run: dict, totals: Totals, should_stop: Callable[[], bool]
) -> bool:
    """Compare ES and local counts hour by hour and re-read only the hours where ELK has more documents."""
    field_name = source["timestamp_field"]
    column = LOCAL_TIME_COLUMN.get(field_name)
    lower, upper = run["lower_bound"], run["upper_bound"]
    local = _local_counts(conn, source["id"], column, lower, upper) if column else {}
    conn.commit()
    complete = True
    for start, end in buckets(lower, upper, RECONCILE_BUCKET_S):
        if should_stop():
            raise Interrupted
        resp = es.count(index=",".join(source["index_patterns"]), **queries.count_body(field_name, start, end)).body
        if queries.is_partial(resp):
            complete = False
            continue
        if column and resp["count"] <= local.get(start, 0):
            continue
        if not _fetch(conn, es, ref, source, field_name, start, end, run["id"], totals, should_stop):
            complete = False
    return complete


def _advance(conn: psycopg.Connection, source: dict, run: dict, now: datetime) -> None:
    sid, upper = source["id"], run["upper_bound"]
    config.ensure_checkpoint(conn, sid)
    if run["run_type"] == RunType.INCREMENTAL:
        conn.execute(
            "UPDATE sync_checkpoints SET ingest_watermark = GREATEST(ingest_watermark, %s), last_success = %s, updated_at = now() "
            "WHERE source_id = %s",
            (upper, now, sid),
        )
    elif run["run_type"] == RunType.ALERT_RESYNC:
        conn.execute(
            "UPDATE sync_checkpoints SET alert_status_watermark = GREATEST(alert_status_watermark, %s), last_success = %s, "
            "updated_at = now() WHERE source_id = %s",
            (upper, now, sid),
        )
    else:
        conn.execute("UPDATE sync_checkpoints SET last_success = %s, updated_at = now() WHERE source_id = %s", (now, sid))
    if run["run_type"] == RunType.BOOTSTRAP:
        plan = config.get_plan(conn, sid)
        if plan and plan["next"] == run["lower_bound"]:
            if upper >= plan["to"]:
                config.clear_plan(conn, sid)
            else:
                config.set_plan(conn, sid, plan["from"], plan["to"], upper)


def execute_run(
    conn: psycopg.Connection,
    es: Elasticsearch,
    source: dict,
    cluster_key: str,
    run: dict,
    clock: Callable[[], datetime],
    should_stop: Callable[[], bool],
    precheck: Callable[[], Access] | None = None,
) -> Outcome:
    ref = SourceRef(source["id"], cluster_key, SourceKind(source["kind"]))
    totals = Totals()
    run = _update(conn, run["id"], status=RunStatus.FETCHING.value, started_at=clock())
    conn.commit()
    access = Access()
    try:
        if precheck:
            access = precheck()
            conn.commit()
            if access.denied:
                return _finish(conn, run, totals, RunStatus.FAILED, clock(), ErrorCode.ELK_FORBIDDEN, access.denied)
        if run["run_type"] == RunType.RECONCILE:
            complete = _reconcile(conn, es, ref, source, run, totals, should_stop)
        else:
            field_name = queries.ALERT_STATUS_FIELD if run["run_type"] == RunType.ALERT_RESYNC else source["timestamp_field"]
            complete = _fetch(conn, es, ref, source, field_name, run["lower_bound"], run["upper_bound"], run["id"], totals, should_stop)
    except Interrupted:
        return _finish(conn, run, totals, RunStatus.CANCELLED, clock(), None, STOPPED_MESSAGE)
    except (EsApiError, TransportError) as exc:
        conn.rollback()
        return _finish(conn, run, totals, RunStatus.FAILED, clock(), classify_exception(exc), redact(str(exc)))
    except Exception as exc:
        conn.rollback()
        log.exception("sync run %s failed", run["id"])
        return _finish(conn, run, totals, RunStatus.FAILED, clock(), ErrorCode.INTERNAL_ERROR, redact(f"{type(exc).__name__}: {exc}"))
    if not complete:
        return _finish(conn, run, totals, RunStatus.PARTIAL, clock(), ErrorCode.SYNC_PARTIAL, PARTIAL_MESSAGE)
    _update(conn, run["id"], status=RunStatus.RAW_COMMITTED.value)
    conn.commit()
    _advance(conn, source, run, clock())
    code = ErrorCode.SYNC_PARTIAL if access.warning else None
    return _finish(conn, run, totals, RunStatus.SUCCEEDED, clock(), code, access.warning)
