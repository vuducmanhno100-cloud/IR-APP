from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from ir_engine.fixtures import load
from ir_engine.ingest.identity import (
    canonical_key,
    cluster_key,
    get_path,
    host_key,
    normalize_guid,
    normalize_index,
    parse_ts,
)

from .conftest import FIXTURES


def test_get_path_handles_nested_and_dotted_keys():
    doc = {"process": {"entity_id": "a"}, "kibana.alert.uuid": "b", "kibana": {"alert": {"rule": {"name": "c"}}}}
    assert get_path(doc, "process.entity_id") == "a"
    assert get_path(doc, "kibana.alert.uuid") == "b"
    assert get_path(doc, "kibana.alert.rule.name") == "c"
    assert get_path(doc, "missing.path") is None


def test_normalize_index_strips_ilm_prefixes():
    assert normalize_index("partial-restored-.ds-logs-x-000001") == ".ds-logs-x-000001"
    assert normalize_index(".ds-logs-x-000001") == ".ds-logs-x-000001"


def test_parse_ts_variants():
    assert parse_ts("2026-10-30T02:00:03Z") == datetime(2026, 10, 30, 2, 0, 3, tzinfo=timezone.utc)
    assert parse_ts("2026-10-30T02:00:03.123456789Z").microsecond == 123456
    assert parse_ts(0) == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert parse_ts(None) is None


def test_identity_helpers():
    assert normalize_guid("{4A1C3B2E-0001-6720-0000-001000000001}") == "4a1c3b2e-0001-6720-0000-001000000001"
    assert host_key({"host": {"id": "ABC", "name": "ws"}}) == "id:abc"
    assert host_key({"agent": {"id": "X"}, "host": {"name": "ws"}}) == "agent:x"
    src = {"winlog": {"record_id": "7", "channel": "Microsoft-Windows-Sysmon/Operational"}}
    assert canonical_key(src, "id:abc") == canonical_key(src, "id:abc")
    assert canonical_key(src, "id:other") != canonical_key(src, "id:abc")
    assert canonical_key({}, "id:abc") is None
    assert cluster_key("6qRk3c", None) == "6qRk3c"
    assert cluster_key("_na_", "Lab Cluster") == "name:lab-cluster"
    with pytest.raises(ValueError):
        cluster_key(None, None)


def test_fixture_load_deduplicates(dsn):
    logs = load(dsn, [FIXTURES], source_name="sysmon", kind="log")
    assert logs == {"files": 5, "fetched": 18, "inserted": 15, "updated": 0, "duplicates": 3}
    again = load(dsn, [FIXTURES], source_name="sysmon", kind="log")
    assert again["inserted"] == 0 and again["duplicates"] == 18
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM hosts").fetchone()["n"] == 2
        states = conn.execute("SELECT DISTINCT process_state FROM raw_events").fetchall()
        assert [s["process_state"] for s in states] == ["PENDING"]


def test_alert_status_update(dsn):
    stats = load(dsn, [FIXTURES / "alerts"], source_name="security-alerts", kind="alert")
    assert stats["inserted"] == 1 and stats["updated"] == 1
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        alert = conn.execute("SELECT status, severity, original_event_ref FROM alert_records").fetchone()
    assert alert["status"] == "acknowledged"
    assert alert["severity"] == "high"
    assert alert["original_event_ref"][0]["type"] == "event"
