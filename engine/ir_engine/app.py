import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .db import Database
from .errors import install_handlers
from .routers import system, tv1, tv2
from .runtime.services import ServiceManager

UI_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


def create_app(session_token: str, db: Database | None, services: ServiceManager | None = None) -> FastAPI:
    app = FastAPI(title="IR Desktop Engine", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.session_token = session_token
    app.state.db = db
    app.state.services = services or ServiceManager()
    app.state.started_at = time.monotonic()
    app.state.request_shutdown = lambda: None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=UI_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    install_handlers(app)
    app.include_router(system.router)
    app.include_router(tv1.router)
    app.include_router(tv2.router)
    return app
