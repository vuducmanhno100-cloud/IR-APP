from fastapi import APIRouter, Body, Depends, Query, Request

from ..auth import require_token
from ..contracts import AlertSeverity, AlertStatus, MatchMode, TermType
from ..deps import get_conn
from ..investigation import cases, service
from ..investigation.service import utc_json
from ..schemas import CaseInput, CaseItemInput, CasePatch

OWNER = "TV2"

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/search/events", tags=["search"])
def search_events(
    type: TermType,
    value: str = Query(..., min_length=1),
    match: MatchMode = MatchMode.EXACT,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    host: str | None = None,
    event_code: str | None = None,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    conn=Depends(get_conn),
):
    return utc_json(service.search(conn, type, value, match, from_, to, host, event_code, cursor, limit))


@router.get("/events/{event_id}", tags=["search"])
def get_event(event_id: int, conn=Depends(get_conn)):
    return utc_json(service.event(conn, event_id))


@router.get("/alerts", tags=["search"])
def list_alerts(
    severity: AlertSeverity | None = None,
    status: AlertStatus | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    conn=Depends(get_conn),
):
    return utc_json(service.alerts(conn, severity, status, from_, to, cursor, limit))


@router.get("/trace/event/{event_id}", tags=["trace"])
def trace_event(
    event_id: int,
    process_id: int | None = None,
    max_depth: int = Query(3, ge=1, le=20),
    max_nodes: int = Query(500, ge=10, le=5000),
    include_inferred: bool = False,
    conn=Depends(get_conn),
):
    return utc_json(service.trace(conn, event_id, process_id, max_depth, max_nodes, include_inferred))


@router.get("/trace/expand/{process_id}", tags=["trace"])
def expand_process(
    process_id: int,
    max_depth: int = Query(3, ge=1, le=20),
    max_nodes: int = Query(500, ge=10, le=5000),
    include_inferred: bool = False,
    conn=Depends(get_conn),
):
    return utc_json(
        service.trace(conn, process_id=process_id, max_depth=max_depth, max_nodes=max_nodes, include_inferred=include_inferred, expand=True)
    )


@router.get("/trace/unresolved", tags=["trace"])
def list_unresolved(status: str | None = None, cursor: str | None = None, limit: int = Query(100, ge=1, le=500), conn=Depends(get_conn)):
    return utc_json(service.unresolved_list(conn, status, cursor, limit))


@router.get("/cases", tags=["cases"])
def list_cases(cursor: str | None = None, limit: int = Query(100, ge=1, le=500), conn=Depends(get_conn)):
    return utc_json(cases.listing(conn, cursor, limit))


@router.post("/cases", tags=["cases"], status_code=201)
def create_case(body: CaseInput, conn=Depends(get_conn)):
    return utc_json(cases.create(conn, body))


@router.get("/cases/{case_id}", tags=["cases"])
def get_case(case_id: int, conn=Depends(get_conn)):
    return utc_json(cases.get(conn, case_id))


@router.patch("/cases/{case_id}", tags=["cases"])
def update_case(case_id: int, body: CasePatch, conn=Depends(get_conn)):
    return utc_json(cases.update(conn, case_id, body))


@router.post("/cases/{case_id}/items", tags=["cases"], status_code=201)
def add_case_item(case_id: int, body: CaseItemInput, conn=Depends(get_conn)):
    return utc_json(cases.add(conn, case_id, body))


@router.delete("/cases/{case_id}/items/{item_id}", tags=["cases"], status_code=204)
def remove_case_item(case_id: int, item_id: int, conn=Depends(get_conn)):
    return cases.remove(conn, case_id, item_id)


@router.post("/cases/{case_id}/export", tags=["cases"])
def export_case(case_id: int, request: Request, body: dict = Body(...), conn=Depends(get_conn)):
    return utc_json(cases.export(conn, case_id, body, data_dir=getattr(request.app.state, "data_dir", None)))
