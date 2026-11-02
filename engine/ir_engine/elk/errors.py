from ..contracts import ErrorCode, SourceState


def classify_status(status: int | None) -> ErrorCode:
    if status is None:
        return ErrorCode.ELK_UNREACHABLE
    if status == 401:
        return ErrorCode.ELK_AUTH_FAILED
    if status == 403:
        return ErrorCode.ELK_FORBIDDEN
    if status == 429:
        return ErrorCode.ELK_RATE_LIMITED
    if 400 <= status < 500:
        return ErrorCode.ELK_BAD_REQUEST
    return ErrorCode.ELK_UNREACHABLE


def classify_exception(exc: BaseException) -> ErrorCode:
    from elastic_transport import ConnectionError as TransportConnectionError
    from elastic_transport import TlsError
    from elasticsearch import ApiError

    if isinstance(exc, TlsError):
        return ErrorCode.ELK_TLS_ERROR
    if isinstance(exc, ApiError):
        return classify_status(exc.meta.status)
    if isinstance(exc, TransportConnectionError):
        return ErrorCode.ELK_UNREACHABLE
    return ErrorCode.ELK_UNREACHABLE


SOURCE_STATE_ON_ERROR = {
    ErrorCode.ELK_AUTH_FAILED: SourceState.AUTH_FAILED,
    ErrorCode.ELK_FORBIDDEN: SourceState.FORBIDDEN,
}

RETRY_ON = {ErrorCode.ELK_UNREACHABLE, ErrorCode.ELK_RATE_LIMITED}
