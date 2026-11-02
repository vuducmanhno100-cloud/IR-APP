from enum import StrEnum

CONTRACT_VERSION = "1.0.0"


class ConnectionState(StrEnum):
    ACTIVE = "ACTIVE"
    AUTH_FAILED = "AUTH_FAILED"
    FORBIDDEN = "FORBIDDEN"
    TLS_ERROR = "TLS_ERROR"
    UNREACHABLE = "UNREACHABLE"


class SourceKind(StrEnum):
    LOG = "log"
    ALERT = "alert"


class SourceState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    AUTH_FAILED = "AUTH_FAILED"
    FORBIDDEN = "FORBIDDEN"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"
    ERROR = "ERROR"


class TimestampStrategy(StrEnum):
    INGESTED = "INGESTED"
    EVENT_TIME = "EVENT_TIME"
    ALERT = "ALERT"


class RunType(StrEnum):
    BOOTSTRAP = "BOOTSTRAP"
    INCREMENTAL = "INCREMENTAL"
    RECONCILE = "RECONCILE"
    ALERT_RESYNC = "ALERT_RESYNC"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    FETCHING = "FETCHING"
    RAW_COMMITTED = "RAW_COMMITTED"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProcessState(StrEnum):
    PENDING = "PENDING"
    NORMALIZED = "NORMALIZED"
    CORRELATED = "CORRELATED"
    UNLINKED = "UNLINKED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class EdgeLevel(StrEnum):
    VERIFIED = "VERIFIED"
    STRONG = "STRONG"
    WEAK = "WEAK"


class UnresolvedStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    CONFLICT = "CONFLICT"


class UnresolvedRelation(StrEnum):
    PARENT = "parent"
    PROCESS_LINK = "process_link"


class LinkType(StrEnum):
    CREATE = "create"
    TERMINATE = "terminate"
    NETWORK = "network"
    DNS = "dns"
    OTHER = "other"


class AttrSource(StrEnum):
    SELF = "self"
    CHILD_EVENT = "child_event"


class TermType(StrEnum):
    IP = "ip"
    DOMAIN = "domain"
    HASH = "hash"
    PROCESS_GUID = "process_guid"
    IMAGE = "image"
    USER = "user"
    HOST = "host"
    COMMAND_LINE = "command_line"
    EVENT_CODE = "event_code"


class MatchMode(StrEnum):
    EXACT = "exact"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    CIDR = "cidr"


class Completeness(StrEnum):
    COMPLETE = "COMPLETE"
    MISSING_PARENT = "MISSING_PARENT"
    TRUNCATED = "TRUNCATED"
    PENDING_DATA = "PENDING_DATA"


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"


class CaseItemType(StrEnum):
    EVENT = "event"
    PROCESS = "process"
    TREE_SNAPSHOT = "tree_snapshot"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


class AlertSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCode(StrEnum):
    ENGINE_UNAUTHORIZED = "ENGINE_UNAUTHORIZED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    ELK_UNREACHABLE = "ELK_UNREACHABLE"
    ELK_TLS_ERROR = "ELK_TLS_ERROR"
    ELK_AUTH_FAILED = "ELK_AUTH_FAILED"
    ELK_FORBIDDEN = "ELK_FORBIDDEN"
    ELK_RATE_LIMITED = "ELK_RATE_LIMITED"
    ELK_BAD_REQUEST = "ELK_BAD_REQUEST"
    SYNC_PARTIAL = "SYNC_PARTIAL"
    SYNC_BUSY = "SYNC_BUSY"
    STORAGE_QUOTA = "STORAGE_QUOTA"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_HTTP = {
    ErrorCode.ENGINE_UNAUTHORIZED: (401, False),
    ErrorCode.VALIDATION_ERROR: (422, False),
    ErrorCode.NOT_FOUND: (404, False),
    ErrorCode.NOT_CONFIGURED: (409, False),
    ErrorCode.NOT_IMPLEMENTED: (501, False),
    ErrorCode.ELK_UNREACHABLE: (502, True),
    ErrorCode.ELK_TLS_ERROR: (502, False),
    ErrorCode.ELK_AUTH_FAILED: (502, False),
    ErrorCode.ELK_FORBIDDEN: (502, False),
    ErrorCode.ELK_RATE_LIMITED: (503, True),
    ErrorCode.ELK_BAD_REQUEST: (502, False),
    ErrorCode.SYNC_PARTIAL: (200, True),
    ErrorCode.SYNC_BUSY: (409, True),
    ErrorCode.STORAGE_QUOTA: (507, False),
    ErrorCode.DB_UNAVAILABLE: (503, True),
    ErrorCode.INTERNAL_ERROR: (500, False),
}

ENUMS = {
    "connection_state": ConnectionState,
    "source_kind": SourceKind,
    "source_state": SourceState,
    "timestamp_strategy": TimestampStrategy,
    "run_type": RunType,
    "run_status": RunStatus,
    "process_state": ProcessState,
    "edge_level": EdgeLevel,
    "unresolved_status": UnresolvedStatus,
    "unresolved_relation": UnresolvedRelation,
    "link_type": LinkType,
    "attr_source": AttrSource,
    "term_type": TermType,
    "match_mode": MatchMode,
    "completeness": Completeness,
    "case_status": CaseStatus,
    "case_item_type": CaseItemType,
    "alert_status": AlertStatus,
    "alert_severity": AlertSeverity,
}
