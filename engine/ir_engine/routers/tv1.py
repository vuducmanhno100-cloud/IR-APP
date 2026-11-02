from fastapi import APIRouter, Body, Depends

from ..auth import require_token
from ..errors import not_implemented

OWNER = "TV1"

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/config/elk", tags=["config"])
def get_elk_connection():
    raise not_implemented(OWNER, "getElkConnection")


@router.put("/config/elk", tags=["config"])
def save_elk_connection(body: dict = Body(...)):
    raise not_implemented(OWNER, "saveElkConnection")


@router.delete("/config/elk", tags=["config"], status_code=204)
def delete_elk_connection():
    raise not_implemented(OWNER, "deleteElkConnection")


@router.post("/config/elk/test", tags=["config"])
def test_elk_connection(body: dict = Body(...)):
    raise not_implemented(OWNER, "testElkConnection")


@router.get("/config/sources", tags=["config"])
def list_sources():
    raise not_implemented(OWNER, "listSources")


@router.put("/config/sources", tags=["config"])
def replace_sources(body: list = Body(...)):
    raise not_implemented(OWNER, "replaceSources")


@router.get("/config/settings", tags=["config"])
def get_settings():
    raise not_implemented(OWNER, "getSettings")


@router.put("/config/settings", tags=["config"])
def update_settings(body: dict = Body(...)):
    raise not_implemented(OWNER, "updateSettings")


@router.get("/sync/status", tags=["sync"])
def get_sync_status():
    raise not_implemented(OWNER, "getSyncStatus")


@router.post("/sync/bootstrap", tags=["sync"], status_code=202)
def start_bootstrap(body: dict = Body(...)):
    raise not_implemented(OWNER, "startBootstrap")


@router.post("/sync/run-now", tags=["sync"], status_code=202)
def run_now(body: dict | None = Body(None)):
    raise not_implemented(OWNER, "runNow")


@router.post("/sync/pause", tags=["sync"], status_code=202)
def pause_sync():
    raise not_implemented(OWNER, "pauseSync")


@router.post("/sync/resume", tags=["sync"], status_code=202)
def resume_sync():
    raise not_implemented(OWNER, "resumeSync")


@router.post("/sync/retry/{run_id}", tags=["sync"], status_code=202)
def retry_run(run_id: int):
    raise not_implemented(OWNER, "retryRun")


@router.get("/sync/runs", tags=["sync"])
def list_runs(source_id: int | None = None, cursor: str | None = None, limit: int = 100):
    raise not_implemented(OWNER, "listRuns")


@router.get("/storage/usage", tags=["storage"])
def get_storage_usage():
    raise not_implemented(OWNER, "getStorageUsage")
