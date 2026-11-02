from datetime import datetime, timezone

from pydantic import TypeAdapter, ValidationError

from ..contracts import ErrorCode
from ..errors import ApiError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def invalid(message: str, **detail) -> ApiError:
    return ApiError(ErrorCode.VALIDATION_ERROR, message, detail)


def parse_body(model, data):
    """Validate a request body without echoing input values back.

    FastAPI's default 422 includes the offending input, which for /config/elk would be the API key.
    """
    try:
        return TypeAdapter(model).validate_python(data)
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        raise invalid("Invalid request", errors=errors) from None
