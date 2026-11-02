import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .contracts import ERROR_HTTP, ErrorCode

log = logging.getLogger("ir_engine")


class ApiError(Exception):
    def __init__(self, code: ErrorCode, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    @property
    def http_status(self) -> int:
        return ERROR_HTTP[self.code][0]

    def body(self) -> dict:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "detail": self.detail,
                "retryable": ERROR_HTTP[self.code][1],
            }
        }


def not_implemented(owner: str, operation: str) -> ApiError:
    return ApiError(ErrorCode.NOT_IMPLEMENTED, f"{operation} is not implemented yet", {"owner": owner, "operation": operation})


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        err = ApiError(ErrorCode.VALIDATION_ERROR, "Invalid request", {"errors": exc.errors()})
        return JSONResponse(err.body(), status_code=err.http_status)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.VALIDATION_ERROR
        err = ApiError(code, str(exc.detail))
        return JSONResponse(err.body(), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")
        err = ApiError(ErrorCode.INTERNAL_ERROR, "Internal error")
        return JSONResponse(err.body(), status_code=err.http_status)
