from collections.abc import Iterator

import psycopg
from fastapi import Request

from .contracts import ErrorCode
from .errors import ApiError
from .runtime.services import ServiceManager


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    db = request.app.state.db
    if db is None:
        raise ApiError(ErrorCode.DB_UNAVAILABLE, "Database is not available")
    with db.connection() as conn:
        yield conn


def get_services(request: Request) -> ServiceManager:
    return request.app.state.services
