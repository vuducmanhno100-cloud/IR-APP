import hmac

from fastapi import Request

from .contracts import ErrorCode
from .errors import ApiError


def require_token(request: Request) -> None:
    expected: str = request.app.state.session_token
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ApiError(ErrorCode.ENGINE_UNAUTHORIZED, "Invalid session token")
