import json
from datetime import datetime, timedelta, timezone

from ir_engine.contracts import ErrorCode, SourceState
from ir_engine.elk import queries
from ir_engine.elk.errors import SOURCE_STATE_ON_ERROR, classify_status
from ir_engine.logs import redact

from .conftest import CONTRACTS

T0 = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)


def test_next_window_applies_overlap_and_safety_lag():
    lower, upper = queries.next_window(T0, T0 + timedelta(minutes=5), overlap_s=120, safety_lag_s=60)
    assert lower == T0 - timedelta(seconds=120)
    assert upper == T0 + timedelta(minutes=4)


def test_next_window_chunks_backlog_and_waits_when_caught_up():
    _, upper = queries.next_window(T0, T0 + timedelta(days=1), 120, 60, max_span_s=3600)
    assert upper == T0 + timedelta(hours=1)
    assert queries.next_window(T0, T0 + timedelta(seconds=30), 120, 60) is None


def test_window_search_body():
    body = queries.window_search("pit-1", "event.ingested", T0, T0 + timedelta(minutes=5), 1000, search_after=["x", 5])
    assert body["sort"] == [{"event.ingested": {"order": "asc"}}, {"_shard_doc": "asc"}]
    assert body["query"]["bool"]["filter"][0]["range"]["event.ingested"] == {
        "gte": "2026-11-02T03:00:00.000Z",
        "lt": "2026-11-02T03:05:00.000Z",
        "format": "strict_date_optional_time",
    }
    assert body["pit"] == {"id": "pit-1", "keep_alive": "2m"}
    assert body["search_after"] == ["x", 5]
    assert "index" not in body


def test_is_partial():
    assert not queries.is_partial({"timed_out": False, "_shards": {"total": 3, "failed": 0}})
    assert queries.is_partial({"timed_out": True, "_shards": {"total": 3, "failed": 0}})
    assert queries.is_partial({"_shards": {"total": 3, "failed": 1}})


def test_examples_match_builders():
    examples = json.loads((CONTRACTS / "elk" / "examples.json").read_text(encoding="utf-8"))
    assert examples["api_key_request"] == queries.api_key_request(
        "ir-desktop-<ten-thanh-vien>",
        ["logs-windows.sysmon_operational-*"],
        [".alerts-security.alerts-*", ".internal.alerts-security.alerts-*"],
    )
    assert examples["has_privileges"] == queries.has_privileges_body(
        ["logs-windows.sysmon_operational-*", ".alerts-security.alerts-*"]
    )
    assert examples["incremental_search"] == queries.window_search(
        "<pit_id>", "event.ingested", T0 - timedelta(seconds=120), T0 + timedelta(minutes=4), 1000, search_after=None
    )
    assert examples["alert_status_search"] == queries.alert_status_search("<pit_id>", T0 - timedelta(hours=1), T0, 500)
    assert examples["field_caps"] == {"log": queries.LOG_REQUIRED_FIELDS, "alert": queries.ALERT_REQUIRED_FIELDS}


def test_error_classification():
    assert classify_status(401) is ErrorCode.ELK_AUTH_FAILED
    assert classify_status(403) is ErrorCode.ELK_FORBIDDEN
    assert classify_status(429) is ErrorCode.ELK_RATE_LIMITED
    assert classify_status(400) is ErrorCode.ELK_BAD_REQUEST
    assert classify_status(503) is ErrorCode.ELK_UNREACHABLE
    assert classify_status(None) is ErrorCode.ELK_UNREACHABLE
    assert SOURCE_STATE_ON_ERROR[ErrorCode.ELK_AUTH_FAILED] is SourceState.AUTH_FAILED


def test_redaction():
    assert "c2VjcmV0" not in redact("headers={'authorization': 'ApiKey c2VjcmV0OmtleQ=='}")
    assert redact("api_key=abc123 other=1") == "api_key=*** other=1"
    assert "tok-value" not in redact('{"token": "tok-value"}')
