import re
import ssl
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from elastic_transport import TransportError
from elasticsearch import ApiError as EsApiError
from elasticsearch import Elasticsearch

from ..contracts import ERROR_HTTP, ErrorCode, SourceKind, TimestampStrategy
from ..errors import ApiError
from ..ingest.identity import cluster_key as make_cluster_key
from ..logs import redact
from . import queries
from .errors import classify_exception

DEFAULT_LOG_PATTERNS = ["logs-windows.sysmon_operational-*"]
DEFAULT_ALERT_PATTERNS = [".alerts-security.alerts-default"]
LOG_SOURCE_NAME = "sysmon"
ALERT_SOURCE_NAME = "security-alerts"
EVENT_TIME_OVERLAP_S = 900
TREE_FIELDS = {"process.entity_id", "process.parent.entity_id", "host.id"}
DEDUP_FIELDS = {"winlog.channel", "winlog.record_id"}
Window = tuple[datetime, datetime] | None
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
# The wizard waits on these calls: one attempt each, so a wrong CA or an unreachable cluster is reported at once
# (the scheduler's client retries; here an unreachable cluster would take 3 x 30 s).
PROBE_OPTIONS = {"max_retries": 0, "request_timeout": 15.0}

MESSAGES = {
    ErrorCode.ELK_AUTH_FAILED: "API key không hợp lệ, đã hết hạn hoặc đã bị thu hồi",
    ErrorCode.ELK_FORBIDDEN: "API key không đủ quyền cho thao tác này",
    ErrorCode.ELK_TLS_ERROR: "Chứng chỉ TLS của cluster không khớp CA đã nhập",
    ErrorCode.ELK_UNREACHABLE: "Không kết nối được tới Elasticsearch",
    ErrorCode.ELK_RATE_LIMITED: "Elasticsearch đang quá tải (HTTP 429)",
    ErrorCode.ELK_BAD_REQUEST: "Elasticsearch từ chối yêu cầu",
}


@dataclass(frozen=True)
class Target:
    url: str
    api_key: str
    ca_pem: str | None
    ca_fingerprint: str | None
    cluster_name: str | None
    log_patterns: list[str]
    alert_patterns: list[str]

    @classmethod
    def from_input(cls, inp) -> "Target":
        patterns = inp.index_patterns
        log = [p for p in (patterns.log if patterns and patterns.log is not None else DEFAULT_LOG_PATTERNS) if p.strip()]
        alert = [p for p in (patterns.alert if patterns and patterns.alert is not None else DEFAULT_ALERT_PATTERNS) if p.strip()]
        fingerprint = inp.ca_fingerprint.replace(":", "").strip().lower() if inp.ca_fingerprint else None
        if not (inp.ca_pem or fingerprint):
            raise ApiError(ErrorCode.VALIDATION_ERROR, "Cần CA certificate (PEM) hoặc SHA-256 fingerprint của CA", {"field": "ca_pem"})
        if fingerprint and not _FINGERPRINT.match(fingerprint):
            raise ApiError(ErrorCode.VALIDATION_ERROR, "Fingerprint phải là SHA-256 dạng hex (64 ký tự)", {"field": "ca_fingerprint"})
        if not log:
            raise ApiError(ErrorCode.VALIDATION_ERROR, "Cần ít nhất một index pattern cho log", {"field": "index_patterns.log"})
        return cls(inp.url.rstrip("/"), inp.api_key, inp.ca_pem or None, fingerprint, inp.cluster_name or None, log, alert)


@dataclass(frozen=True)
class SaveCheck:
    authenticated: dict
    cluster: dict
    ingested_present: bool
    api_key_expires_at: datetime | None


def elk_error(exc: BaseException) -> ApiError:
    code = classify_exception(exc)
    detail: dict = {"reason": redact(str(exc))[:500]}
    status = getattr(getattr(exc, "meta", None), "status", None)
    if status:
        detail["http_status"] = status
    return ApiError(code, MESSAGES.get(code, "Lỗi khi gọi Elasticsearch"), detail)


@contextmanager
def open_client(factory: Callable[..., Elasticsearch], target: Target) -> Iterator[Elasticsearch]:
    try:
        es = factory(target.url, target.api_key, ca_pem=target.ca_pem, ca_fingerprint=target.ca_fingerprint, **PROBE_OPTIONS)
    except (ssl.SSLError, ValueError) as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "CA certificate không đọc được", {"field": "ca_pem", "reason": str(exc)[:200]}) from None
    try:
        yield es
    except (EsApiError, TransportError) as exc:
        raise elk_error(exc) from None
    finally:
        es.close()


def authenticate(es: Elasticsearch) -> dict:
    resp = es.security.authenticate()
    key = resp.get("api_key") or {}
    return _compact({"api_key_id": key.get("id"), "api_key_name": key.get("name"), "username": resp.get("username")})


def identify(es: Elasticsearch, cluster_name: str | None) -> dict:
    try:
        info = es.info()
    except EsApiError as exc:
        if exc.meta.status != 403:
            raise
        info = None
    uuid = info.get("cluster_uuid") if info else None
    name = info.get("cluster_name") if info else cluster_name
    version = info["version"]["number"] if info else None
    try:
        key = make_cluster_key(uuid, name or cluster_name)
    except ValueError:
        key = None
    return {"cluster_key": key, "uuid": uuid, "name": name, "version": version}


def privileges(es: Elasticsearch, patterns: list[str]) -> list[dict]:
    if not patterns:
        return []
    resp = es.security.has_privileges(index=[{"names": patterns, "privileges": queries.LOG_PRIVILEGES}])
    index = resp.get("index", {})
    return [
        {"pattern": p, "read": bool(index.get(p, {}).get("read")), "view_index_metadata": bool(index.get(p, {}).get("view_index_metadata"))}
        for p in patterns
    ]


def missing_privileges(rows: list[dict]) -> list[str]:
    return [r["pattern"] for r in rows if not (r["read"] and r["view_index_metadata"])]


def field_presence(es: Elasticsearch, pattern: str, wanted: list[str]) -> list[dict]:
    caps = es.field_caps(index=pattern, fields=",".join(wanted), ignore_unavailable=True, allow_no_indices=True)
    present = caps.get("fields", {})
    return [{"pattern": pattern, "field": f, "present": f in present} for f in wanted]


def count(es: Elasticsearch, pattern: str, field: str | None = None, window: Window = None) -> int:
    body = queries.count_body(field, *window) if field and window else {}
    return int(es.count(index=pattern, ignore_unavailable=True, allow_no_indices=True, **body)["count"])


def api_key_expiry(es: Elasticsearch, key_id: str | None) -> datetime | None:
    """Best effort: a read-only key usually may not list API keys, so a failure just means "unknown"."""
    if not key_id:
        return None
    try:
        resp = es.security.get_api_key(id=key_id)
    except (EsApiError, TransportError):
        return None
    for key in resp.get("api_keys", []):
        if key.get("id") == key_id and key.get("expiration"):
            return datetime.fromtimestamp(key["expiration"] / 1000, tz=timezone.utc)
    return None


def source_spec(name: str, kind: SourceKind, patterns: list[str], strategy: TimestampStrategy, field: str) -> dict:
    overlap = EVENT_TIME_OVERLAP_S if strategy == TimestampStrategy.EVENT_TIME else 120
    return {
        "name": name,
        "kind": kind.value,
        "index_patterns": list(patterns),
        "timestamp_strategy": strategy.value,
        "timestamp_field": field,
        "overlap_s": overlap,
        "safety_lag_s": 60,
        "batch_size": 1000,
        "enabled": True,
    }


def suggest_sources(log_patterns: list[str], alert_patterns: list[str], ingested_present: bool) -> list[dict]:
    specs = []
    if log_patterns:
        strategy, field = (
            (TimestampStrategy.INGESTED, "event.ingested") if ingested_present else (TimestampStrategy.EVENT_TIME, "@timestamp")
        )
        specs.append(source_spec(LOG_SOURCE_NAME, SourceKind.LOG, log_patterns, strategy, field))
    if alert_patterns:
        specs.append(source_spec(ALERT_SOURCE_NAME, SourceKind.ALERT, alert_patterns, TimestampStrategy.ALERT, "@timestamp"))
    return specs


class _Findings:
    def __init__(self):
        self.problems: list[tuple[ErrorCode, str]] = []
        self.warnings: list[str] = []

    def problem(self, code: ErrorCode, message: str) -> None:
        self.problems.append((code, message))

    def error_body(self) -> dict | None:
        if not self.problems:
            return None
        code, message = self.problems[0]
        return {"code": code.value, "message": message, "detail": {}, "retryable": ERROR_HTTP[code][1]}

    def all_warnings(self) -> list[str]:
        return [m for _, m in self.problems] + self.warnings


def _cluster_problems(cluster: dict, findings: _Findings) -> None:
    if cluster["cluster_key"] is None:
        findings.problem(ErrorCode.VALIDATION_ERROR, "Key không có quyền cluster 'monitor': hãy nhập tên cluster")
    version = cluster["version"]
    if version and not version.startswith("9."):
        findings.problem(ErrorCode.ELK_BAD_REQUEST, f"Elasticsearch {version} không được hỗ trợ; ứng dụng cần bản 9.x")


def _check_log_pattern(es: Elasticsearch, pattern: str, findings: _Findings, counts: list, fields: list, history: Window) -> bool:
    n = count(es, pattern)
    entry = {"pattern": pattern, "count": n, **({"history_count": 0} if history else {})}
    counts.append(entry)
    if n == 0:
        findings.warnings.append(f"{pattern}: chưa có document nào")
        return True
    rows = field_presence(es, pattern, queries.LOG_REQUIRED_FIELDS)
    fields.extend(rows)
    absent = {r["field"] for r in rows if not r["present"]}
    if history:
        entry["history_count"] = count(es, pattern, "@timestamp" if "event.ingested" in absent else "event.ingested", history)
    if "@timestamp" in absent:
        findings.problem(ErrorCode.VALIDATION_ERROR, f"{pattern}: không có trường @timestamp")
    if absent & TREE_FIELDS:
        findings.warnings.append(f"{pattern}: thiếu {', '.join(sorted(absent & TREE_FIELDS))}; không dựng được cây process cho dữ liệu này")
    if absent & DEDUP_FIELDS:
        findings.warnings.append(f"{pattern}: thiếu {', '.join(sorted(absent & DEDUP_FIELDS))}; chỉ chống trùng theo _id")
    if "event.ingested" in absent:
        findings.warnings.append(f"{pattern}: thiếu event.ingested; đồng bộ theo @timestamp (EVENT_TIME), log đến quá trễ có thể bị sót")
        return False
    return True


def _check_alert_pattern(es: Elasticsearch, pattern: str, findings: _Findings, counts: list, fields: list, history: Window) -> None:
    n = count(es, pattern)
    counts.append(
        {"pattern": pattern, "count": n, **({"history_count": count(es, pattern, "@timestamp", history) if n else 0} if history else {})}
    )
    if n == 0:
        findings.warnings.append(f"{pattern}: chưa có alert (kiểm tra detection rules hoặc tên space)")
        return
    rows = field_presence(es, pattern, queries.ALERT_REQUIRED_FIELDS)
    fields.extend(rows)
    absent = [r["field"] for r in rows if not r["present"]]
    if absent:
        findings.warnings.append(f"{pattern}: thiếu {', '.join(absent)}")


def run_test(es: Elasticsearch, target: Target, history: Window = None) -> dict:
    """POST /config/elk/test. Connection-level failures (auth, TLS, network) propagate as errors;
    everything found after authentication is reported in the result instead. With `history` (wizard step 5)
    each pattern also gets the number of documents a bootstrap of that range would fetch."""
    authenticated = authenticate(es)
    cluster = identify(es, target.cluster_name)
    findings = _Findings()
    _cluster_problems(cluster, findings)
    privs = privileges(es, target.log_patterns + target.alert_patterns)
    missing = missing_privileges(privs)
    if missing:
        findings.problem(ErrorCode.ELK_FORBIDDEN, f"Key thiếu quyền read/view_index_metadata trên: {', '.join(missing)}")
    counts: list[dict] = []
    fields: list[dict] = []
    ingested = True
    for pattern in target.log_patterns:
        if pattern not in missing:
            ingested = _guarded(findings, lambda p=pattern: _check_log_pattern(es, p, findings, counts, fields, history), True) and ingested
    for pattern in target.alert_patterns:
        if pattern not in missing:
            _guarded(findings, lambda p=pattern: _check_alert_pattern(es, p, findings, counts, fields, history), None)
    return {
        "ok": not findings.problems,
        "authenticated": authenticated,
        "cluster": _compact(cluster),
        "privileges": privs,
        "fields": fields,
        "counts": counts,
        "suggested_sources": suggest_sources(target.log_patterns, target.alert_patterns, ingested),
        "warnings": findings.all_warnings(),
        "error": findings.error_body(),
    }


def verify_for_save(es: Elasticsearch, target: Target) -> SaveCheck:
    authenticated = authenticate(es)
    cluster = identify(es, target.cluster_name)
    if cluster["cluster_key"] is None:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "Key không có quyền cluster 'monitor': hãy nhập tên cluster", {"field": "cluster_name"})
    if cluster["version"] and not cluster["version"].startswith("9."):
        raise ApiError(ErrorCode.ELK_BAD_REQUEST, f"Elasticsearch {cluster['version']} không được hỗ trợ; ứng dụng cần bản 9.x")
    missing = missing_privileges(privileges(es, target.log_patterns + target.alert_patterns))
    if missing:
        raise ApiError(ErrorCode.ELK_FORBIDDEN, "API key thiếu quyền read/view_index_metadata", {"patterns": missing})
    ingested = all(_has_ingested(es, p) for p in target.log_patterns)
    return SaveCheck(authenticated, cluster, ingested, api_key_expiry(es, authenticated.get("api_key_id")))


def _has_ingested(es: Elasticsearch, pattern: str) -> bool:
    if count(es, pattern) == 0:
        return True
    return field_presence(es, pattern, ["event.ingested"])[0]["present"]


def _guarded(findings: _Findings, check: Callable, default):
    try:
        return check()
    except (EsApiError, TransportError) as exc:
        error = elk_error(exc)
        findings.problem(error.code, f"{error.message}: {error.detail.get('reason', '')}"[:300])
        return default


def _compact(values: dict) -> dict:
    return {k: v for k, v in values.items() if v is not None or k in ("uuid", "name", "version")}
