from fastapi import APIRouter, Body, Depends, Query

from ..auth import require_token
from ..contracts import AlertSeverity, AlertStatus, MatchMode, TermType
from ..errors import not_implemented

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
):
    raise not_implemented(OWNER, "searchEvents")


@router.get("/events/{event_id}", tags=["search"])
def get_event(event_id: int):
    raise not_implemented(OWNER, "getEvent")


@router.get("/alerts", tags=["search"])
def list_alerts(
    severity: AlertSeverity | None = None,
    status: AlertStatus | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    raise not_implemented(OWNER, "listAlerts")


@router.get("/trace/event/{event_id}", tags=["trace"])
def trace_event(
    event_id: int,
    process_id: int | None = None,
    max_depth: int = Query(3, ge=1, le=20),
    max_nodes: int = Query(500, ge=10, le=5000),
    include_inferred: bool = False,
):
    raise not_implemented(OWNER, "traceEvent")


@router.get("/trace/expand/{process_id}", tags=["trace"])
def expand_process(
    process_id: int,
    max_depth: int = Query(3, ge=1, le=20),
    max_nodes: int = Query(500, ge=10, le=5000),
    include_inferred: bool = False,
):
    raise not_implemented(OWNER, "expandProcess")


@router.get("/trace/unresolved", tags=["trace"])
def list_unresolved(status: str | None = None, cursor: str | None = None, limit: int = Query(100, ge=1, le=500)):
    raise not_implemented(OWNER, "listUnresolved")


@router.get("/cases", tags=["cases"])
def list_cases(cursor: str | None = None, limit: int = Query(100, ge=1, le=500)):
    raise not_implemented(OWNER, "listCases")


@router.post("/cases", tags=["cases"], status_code=201)
def create_case(body: dict = Body(...)):
    raise not_implemented(OWNER, "createCase")


@router.get("/cases/{case_id}", tags=["cases"])
def get_case(case_id: int):
    raise not_implemented(OWNER, "getCase")


@router.patch("/cases/{case_id}", tags=["cases"])
def update_case(case_id: int, body: dict = Body(...)):
    raise not_implemented(OWNER, "updateCase")


@router.post("/cases/{case_id}/items", tags=["cases"], status_code=201)
def add_case_item(case_id: int, body: dict = Body(...)):
    raise not_implemented(OWNER, "addCaseItem")


@router.delete("/cases/{case_id}/items/{item_id}", tags=["cases"], status_code=204)
def remove_case_item(case_id: int, item_id: int):
    raise not_implemented(OWNER, "removeCaseItem")


@router.post("/cases/{case_id}/export", tags=["cases"])
def export_case(case_id: int, body: dict = Body(...)):
    raise not_implemented(OWNER, "exportCase")
