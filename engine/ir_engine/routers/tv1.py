from datetime import datetime

import psycopg
from fastapi import APIRouter, Body, Depends, Query, Request, Response

from .. import audit
from ..auth import require_token
from ..credentials import shared_store
from ..deps import get_conn, get_services
from ..elk import probe
from ..elk.client import make_client
from ..runtime.services import ServiceManager
from ..schemas import ElkConnectionInput, Settings, SourceInput
from ..storage.usage import usage
from ..sync import config, control
from ..sync.common import parse_body, utcnow

router = APIRouter(dependencies=[Depends(require_token)])


def secret_store():
    return shared_store()


def client_factory():
    return make_client


def clock() -> datetime:
    return utcnow()


def _wake_sync(services: ServiceManager) -> None:
    service = services.get("sync")
    if service:
        service.wake()


@router.get("/config/elk", tags=["config"])
def get_elk_connection(conn: psycopg.Connection = Depends(get_conn)):
    return config.connection_view(config.require_connection(conn))


@router.put("/config/elk", tags=["config"])
def save_elk_connection(
    body: dict = Body(...),
    conn: psycopg.Connection = Depends(get_conn),
    store=Depends(secret_store),
    factory=Depends(client_factory),
    services: ServiceManager = Depends(get_services),
):
    target = probe.Target.from_input(parse_body(ElkConnectionInput, body))
    with probe.open_client(factory, target) as es:
        check = probe.verify_for_save(es, target)
    row, previous = config.save_connection(conn, store, target, check)
    audit.record(
        conn,
        "config.elk.save",
        f"elk_connections:{row['id']}",
        {
            "url": row["url"],
            "cluster_key": row["cluster_key"],
            "api_key_id": row["api_key_id"],
            "api_key_name": row["api_key_name"],
            "previous_connection_id": previous["id"] if previous else None,
            "key_changed": bool(previous) and previous["api_key_id"] != row["api_key_id"],
        },
    )
    conn.commit()
    _wake_sync(services)
    return config.connection_view(row)


@router.delete("/config/elk", tags=["config"], status_code=204)
def delete_elk_connection(conn: psycopg.Connection = Depends(get_conn), store=Depends(secret_store)):
    row = config.forget_connection(conn, store)
    if row:
        audit.record(conn, "config.elk.delete", f"elk_connections:{row['id']}", {"cluster_key": row["cluster_key"]})
    conn.commit()
    return Response(status_code=204)


@router.post("/config/elk/test", tags=["config"])
def test_elk_connection(body: dict = Body(...), factory=Depends(client_factory)):
    target = probe.Target.from_input(parse_body(ElkConnectionInput, body))
    with probe.open_client(factory, target) as es:
        return probe.run_test(es, target)


@router.get("/config/sources", tags=["config"])
def list_sources(conn: psycopg.Connection = Depends(get_conn)):
    connection = config.active_connection(conn)
    return [config.source_view(s) for s in config.list_sources(conn, connection["id"])] if connection else []


@router.put("/config/sources", tags=["config"])
def replace_sources(body: list = Body(...), conn: psycopg.Connection = Depends(get_conn), services: ServiceManager = Depends(get_services)):
    specs = [s.model_dump(mode="json") for s in parse_body(list[SourceInput], body)]
    connection = config.require_connection(conn)
    rows = config.replace_sources(conn, connection["id"], specs)
    audit.record(conn, "config.sources.replace", f"elk_connections:{connection['id']}", {"sources": [s["name"] for s in specs]})
    conn.commit()
    _wake_sync(services)
    return [config.source_view(s) for s in rows]


@router.get("/config/settings", tags=["config"])
def get_settings(conn: psycopg.Connection = Depends(get_conn)):
    return config.get_settings(conn)


@router.put("/config/settings", tags=["config"])
def update_settings(body: dict = Body(...), conn: psycopg.Connection = Depends(get_conn)):
    values = parse_body(Settings, body).model_dump()
    before = config.get_settings(conn)
    after = config.update_settings(conn, values)
    changed = {k: {"from": before[k], "to": v} for k, v in after.items() if before[k] != v}
    audit.record(conn, "config.settings.update", "app_settings", changed)
    conn.commit()
    return after


@router.get("/sync/status", tags=["sync"])
def get_sync_status(
    conn: psycopg.Connection = Depends(get_conn), services: ServiceManager = Depends(get_services), now: datetime = Depends(clock)
):
    return control.status(conn, services.get("sync"), now)


@router.post("/sync/bootstrap", tags=["sync"], status_code=202)
def start_bootstrap(
    body: dict = Body(...),
    conn: psycopg.Connection = Depends(get_conn),
    services: ServiceManager = Depends(get_services),
    now: datetime = Depends(clock),
):
    runs = control.bootstrap(conn, parse_body(control.BootstrapRequest, body), now)
    _wake_sync(services)
    return {"runs": runs}


@router.post("/sync/run-now", tags=["sync"], status_code=202)
def run_now(
    body: dict | None = Body(None),
    conn: psycopg.Connection = Depends(get_conn),
    services: ServiceManager = Depends(get_services),
    now: datetime = Depends(clock),
):
    runs = control.run_now(conn, parse_body(control.RunNowRequest, body or {}), now)
    _wake_sync(services)
    return {"runs": runs}


@router.post("/sync/pause", tags=["sync"], status_code=202)
def pause_sync(conn: psycopg.Connection = Depends(get_conn), services: ServiceManager = Depends(get_services)):
    control.pause(conn, services.get("sync"))
    return Response(status_code=202)


@router.post("/sync/resume", tags=["sync"], status_code=202)
def resume_sync(conn: psycopg.Connection = Depends(get_conn), services: ServiceManager = Depends(get_services)):
    control.resume(conn, services.get("sync"))
    return Response(status_code=202)


@router.post("/sync/retry/{run_id}", tags=["sync"], status_code=202)
def retry_run(run_id: int, conn: psycopg.Connection = Depends(get_conn), services: ServiceManager = Depends(get_services)):
    run = control.retry(conn, run_id)
    _wake_sync(services)
    return run


@router.get("/sync/runs", tags=["sync"])
def list_runs(
    source_id: int | None = None,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    conn: psycopg.Connection = Depends(get_conn),
):
    return control.list_runs(conn, source_id, cursor, limit)


@router.get("/storage/usage", tags=["storage"])
def get_storage_usage(request: Request, conn: psycopg.Connection = Depends(get_conn), now: datetime = Depends(clock)):
    quota = config.get_settings(conn)["quota_bytes"]
    return usage(conn, getattr(request.app.state, "data_dir", None), quota, with_growth=True, now=now)
