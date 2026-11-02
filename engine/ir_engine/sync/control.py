import base64
from datetime import datetime, timedelta

import psycopg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .. import audit
from ..contracts import ErrorCode, RunType, SourceState
from ..elk import queries
from ..errors import ApiError
from ..runtime.services import BackgroundService
from . import config, runner
from .common import invalid, iso
from .runner import LOCAL_TIME_COLUMN, run_view
from .scheduler import BLOCKED_CONNECTION, INCREMENTAL_SPAN_S, chunk_span, prepare_checkpoint

FUTURE_TOLERANCE = timedelta(minutes=5)


class BootstrapRequest(BaseModel):
    """Inline request body of POST /sync/bootstrap (the generator only emits named schemas)."""

    model_config = ConfigDict(populate_by_name=True)
    from_: AwareDatetime = Field(alias="from")
    to: AwareDatetime
    source_ids: list[int] | None = None


class RunNowRequest(BaseModel):
    source_ids: list[int] | None = None


def busy(message: str, **detail) -> ApiError:
    return ApiError(ErrorCode.SYNC_BUSY, message, detail)


def encode_cursor(run_id: int) -> str:
    return base64.urlsafe_b64encode(str(run_id).encode()).decode()


def decode_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError):
        raise invalid("Cursor không hợp lệ", field="cursor") from None


def target_sources(conn: psycopg.Connection, connection: dict, source_ids: list[int] | None) -> list[dict]:
    sources = [s for s in config.list_sources(conn, connection["id"]) if s["enabled"]]
    if source_ids is None:
        return sources
    known = {s["id"]: s for s in sources}
    unknown = [sid for sid in source_ids if sid not in known]
    if unknown:
        raise invalid("Source không tồn tại hoặc đang tắt", source_ids=unknown)
    return [known[sid] for sid in dict.fromkeys(source_ids)]


def scheduler_state(service: BackgroundService | None, connection: dict | None, sources: list[dict]) -> str:
    enabled = [s for s in sources if s["enabled"]]
    if connection is None or connection["state"] in BLOCKED_CONNECTION:
        return "blocked"
    if enabled and all(s["state"] == SourceState.QUOTA_BLOCKED for s in enabled):
        return "blocked"
    if service is None or service.state != "running":
        return "paused"
    return "running"


def source_status(conn: psycopg.Connection, source: dict, now: datetime) -> dict:
    cp = conn.execute("SELECT * FROM sync_checkpoints WHERE source_id = %s", (source["id"],)).fetchone() or {}
    column = LOCAL_TIME_COLUMN.get(source["timestamp_field"], "event_time")
    backlog = conn.execute(
        f"""
        SELECT count(*) FILTER (WHERE process_state = 'PENDING') AS pending,
               count(*) FILTER (WHERE process_state = 'FAILED') AS failed,
               min({column}) AS oldest
        FROM raw_events WHERE source_id = %s AND process_state IN ('PENDING','FAILED')
        """,
        (source["id"],),
    ).fetchone()
    ingest_watermark = cp.get("ingest_watermark")
    active = runner.active_run(conn, source["id"])
    last = runner.last_finished(conn, source["id"])
    last_error = None
    if last and last[0]["error_code"]:
        last_error = {"code": last[0]["error_code"], "message": last[0]["error"] or "", "at": iso(last[0]["finished_at"])}
    return {
        "source_id": source["id"],
        "name": source["name"],
        "kind": source["kind"],
        "state": source["state"],
        "ingest_watermark": iso(ingest_watermark),
        "process_watermark": iso(backlog["oldest"] or ingest_watermark),
        "lag_s": max(int((now - ingest_watermark).total_seconds()), 0) if ingest_watermark else None,
        "active_run": run_view(active) if active else None,
        "pending_events": backlog["pending"],
        "failed_events": backlog["failed"],
        "last_success": iso(cp.get("last_success")),
        "last_error": last_error,
    }


def status(conn: psycopg.Connection, service: BackgroundService | None, now: datetime) -> dict:
    connection = config.active_connection(conn)
    sources = config.list_sources(conn, connection["id"]) if connection else []
    return {
        "scheduler": scheduler_state(service, connection, sources),
        "sources": [source_status(conn, s, now) for s in sources if s["enabled"]],
    }


def list_runs(conn: psycopg.Connection, source_id: int | None, cursor: str | None, limit: int) -> dict:
    before = decode_cursor(cursor)
    rows = conn.execute(
        f"""
        SELECT {runner.RUN_FIELDS} FROM sync_runs
        WHERE (%(sid)s::bigint IS NULL OR source_id = %(sid)s) AND (%(before)s::bigint IS NULL OR id < %(before)s)
        ORDER BY id DESC LIMIT %(limit)s
        """,
        {"sid": source_id, "before": before, "limit": limit + 1},
    ).fetchall()
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1]["id"]) if len(rows) > limit else None
    return {"items": [run_view(r) for r in items], "next_cursor": next_cursor}


def bootstrap(conn: psycopg.Connection, request: BootstrapRequest, now: datetime) -> list[dict]:
    connection = config.require_connection(conn)
    lower, upper = request.from_, request.to
    if lower >= upper:
        raise invalid("'from' phải nhỏ hơn 'to'", field="from")
    if upper > now + FUTURE_TOLERANCE:
        raise invalid("'to' nằm ở tương lai", field="to")
    settings = config.get_settings(conn)
    oldest = now - timedelta(days=settings["retention_days"])
    if lower < oldest:
        raise invalid(f"'from' cũ hơn thời gian lưu giữ ({settings['retention_days']} ngày); dữ liệu sẽ bị xóa ngay", field="from")
    sources = target_sources(conn, connection, request.source_ids)
    taken = [s["id"] for s in sources if runner.active_run(conn, s["id"]) or config.get_plan(conn, s["id"])]
    if taken:
        raise busy("Source đang có run hoặc bootstrap chưa xong", source_ids=taken)
    runs = []
    for source in sources:
        config.set_plan(conn, source["id"], lower, upper, lower)
        cp = config.ensure_checkpoint(conn, source["id"])
        if cp["ingest_watermark"] is None:
            conn.execute("UPDATE sync_checkpoints SET ingest_watermark = %s WHERE source_id = %s", (upper, source["id"]))
        if source["state"] == SourceState.ACTIVE:
            first = min(lower + timedelta(seconds=chunk_span(source)), upper)
            runs.append(runner.create_run(conn, source["id"], RunType.BOOTSTRAP, lower, first))
    audit.record(conn, "sync.bootstrap", "source_configs", {"from": iso(lower), "to": iso(upper), "source_ids": [s["id"] for s in sources]})
    conn.commit()
    return [run_view(r) for r in runs]


def run_now(conn: psycopg.Connection, request: RunNowRequest, now: datetime) -> list[dict]:
    connection = config.require_connection(conn)
    if connection["state"] in BLOCKED_CONNECTION:
        raise busy(f"Kết nối ELK đang ở trạng thái {connection['state']}; nhập lại API key", state=connection["state"])
    settings = config.get_settings(conn)
    runs, taken = [], []
    for source in target_sources(conn, connection, request.source_ids):
        if source["state"] != SourceState.ACTIVE or runner.active_run(conn, source["id"]):
            taken.append(source["id"])
            continue
        cp = prepare_checkpoint(conn, source, settings, now)
        window = queries.next_window(cp["ingest_watermark"], now, source["overlap_s"], source["safety_lag_s"], INCREMENTAL_SPAN_S)
        if window:
            try:
                runs.append(runner.create_run(conn, source["id"], RunType.INCREMENTAL, *window))
            except runner.RunBusy:
                taken.append(source["id"])
    if taken and not runs:
        raise busy("Không source nào chạy được lúc này (đang có run hoặc không ở trạng thái ACTIVE)", source_ids=taken)
    audit.record(conn, "sync.run_now", "source_configs", {"source_ids": [r["source_id"] for r in runs]})
    conn.commit()
    return [run_view(r) for r in runs]


def retry(conn: psycopg.Connection, run_id: int) -> dict:
    run = runner.get_run(conn, run_id)
    if run is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"Run {run_id} không tồn tại")
    if run["status"] not in runner.RETRYABLE_STATUSES:
        raise busy(f"Chỉ thử lại run FAILED, PARTIAL hoặc CANCELLED (run này đang {run['status']})", status=run["status"])
    connection = config.active_connection(conn)
    source = config.get_source(conn, run["source_id"])
    if connection is None or source is None or source["connection_id"] != connection["id"] or not source["enabled"]:
        raise busy("Source của run này không còn thuộc kết nối đang dùng hoặc đã bị tắt")
    if source["state"] != SourceState.ACTIVE:
        raise busy(f"Source đang ở trạng thái {source['state']}", state=source["state"])
    try:
        new = runner.create_run(conn, source["id"], RunType(run["run_type"]), run["lower_bound"], run["upper_bound"])
    except runner.RunBusy:
        raise busy("Source đang có run khác", source_id=source["id"]) from None
    audit.record(conn, "sync.retry", f"sync_runs:{run_id}", {"new_run_id": new["id"]})
    conn.commit()
    return run_view(new)


def pause(conn: psycopg.Connection, service: BackgroundService | None) -> None:
    config.set_value(conn, config.SYNC_PAUSED, True)
    audit.record(conn, "sync.pause")
    conn.commit()
    if service:
        service.pause()


def resume(conn: psycopg.Connection, service: BackgroundService | None) -> None:
    config.require_connection(conn)
    config.set_value(conn, config.SYNC_PAUSED, False)
    audit.record(conn, "sync.resume")
    conn.commit()
    if service:
        service.resume()
        service.wake()
