import time

from fastapi import APIRouter, Depends, Request

from .. import __version__
from ..auth import require_token
from ..contracts import CONTRACT_VERSION
from ..db.migrate import current_version

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_token)])


@router.get("/health")
def get_health(request: Request):
    state = request.app.state
    db_status, schema_version = "down", None
    if state.db is not None:
        try:
            with state.db.connection() as conn:
                schema_version = current_version(conn)
            db_status = "up"
        except Exception:
            db_status = "down"
    return {
        "status": "ok" if db_status == "up" else "degraded",
        "engine_version": __version__,
        "contract_version": CONTRACT_VERSION,
        "db": {"status": db_status, "schema_version": schema_version},
        "uptime_s": int(time.monotonic() - state.started_at),
        "workers": state.services.status(),
    }


@router.post("/shutdown", status_code=202)
def shutdown(request: Request):
    request.app.state.request_shutdown()
    return {"accepted": True}
