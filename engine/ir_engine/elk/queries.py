from datetime import datetime, timedelta, timezone

PIT_KEEP_ALIVE = "2m"
LOG_PRIVILEGES = ["read", "view_index_metadata"]

LOG_REQUIRED_FIELDS = [
    "@timestamp",
    "event.ingested",
    "event.code",
    "process.entity_id",
    "process.parent.entity_id",
    "host.id",
    "winlog.channel",
    "winlog.record_id",
]

ALERT_REQUIRED_FIELDS = [
    "@timestamp",
    "kibana.alert.uuid",
    "kibana.alert.rule.name",
    "kibana.alert.severity",
    "kibana.alert.workflow_status",
    "kibana.alert.workflow_status_updated_at",
    "kibana.alert.original_time",
    "kibana.alert.ancestors.id",
]

ALERT_STATUS_FIELD = "kibana.alert.workflow_status_updated_at"


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def next_window(
    watermark: datetime,
    now: datetime,
    overlap_s: int,
    safety_lag_s: int,
    max_span_s: int = 3600,
) -> tuple[datetime, datetime] | None:
    lower = watermark - timedelta(seconds=overlap_s)
    upper = min(now - timedelta(seconds=safety_lag_s), watermark + timedelta(seconds=max_span_s))
    if upper <= watermark:
        return None
    return lower, upper


def range_filter(field: str, lower: datetime, upper: datetime) -> dict:
    return {"range": {field: {"gte": iso(lower), "lt": iso(upper), "format": "strict_date_optional_time"}}}


def open_pit_params(keep_alive: str = PIT_KEEP_ALIVE) -> dict:
    return {"keep_alive": keep_alive, "ignore_unavailable": False}


def is_partial(response: dict) -> bool:
    shards = response.get("_shards") or {}
    return bool(response.get("timed_out")) or shards.get("failed", 0) > 0 or shards.get("skipped", 0) > shards.get("total", 0)


def window_search(
    pit_id: str,
    time_field: str,
    lower: datetime,
    upper: datetime,
    size: int,
    search_after: list | None = None,
    keep_alive: str = PIT_KEEP_ALIVE,
) -> dict:
    body = {
        "size": size,
        "pit": {"id": pit_id, "keep_alive": keep_alive},
        "query": {"bool": {"filter": [range_filter(time_field, lower, upper)]}},
        "sort": [{time_field: {"order": "asc"}}, {"_shard_doc": "asc"}],
        "track_total_hits": False,
    }
    if search_after is not None:
        body["search_after"] = search_after
    return body


def alert_status_search(pit_id: str, lower: datetime, upper: datetime, size: int, search_after: list | None = None) -> dict:
    return window_search(pit_id, ALERT_STATUS_FIELD, lower, upper, size, search_after)


def count_body(time_field: str, lower: datetime, upper: datetime) -> dict:
    return {"query": {"bool": {"filter": [range_filter(time_field, lower, upper)]}}}


def has_privileges_body(patterns: list[str], with_monitor: bool = True) -> dict:
    body = {"index": [{"names": patterns, "privileges": LOG_PRIVILEGES}]}
    if with_monitor:
        body["cluster"] = ["monitor"]
    return body


def api_key_request(name: str, log_patterns: list[str], alert_patterns: list[str], expiration: str = "30d", with_monitor: bool = True) -> dict:
    return {
        "name": name,
        "expiration": expiration,
        "role_descriptors": {
            "ir_desktop_read": {
                "cluster": ["monitor"] if with_monitor else [],
                "indices": [{"names": log_patterns + alert_patterns, "privileges": LOG_PRIVILEGES}],
            }
        },
        "metadata": {"application": "ir-desktop"},
    }
