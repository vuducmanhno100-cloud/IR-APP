from datetime import datetime, timezone

import pytest

from ir_engine.credentials import elk_ref
from ir_engine.testing.contract import assert_conforms
from ir_engine.testing.fake_es import FakeElasticsearch, FakeState

from .conftest import FIXTURES
from .tv1_support import API_KEY, NOW, Clock, Harness, connection_input


@pytest.fixture
def fake():
    with FakeElasticsearch() as server:
        server.load_ndjson(FIXTURES)
        yield server


@pytest.fixture
def h(dsn, fake):
    harness = Harness(dsn, fake, Clock(NOW))
    yield harness
    harness.close()


def secrets_in_database(h: Harness, secret: str) -> list[str]:
    tables = ["elk_connections", "source_configs", "app_settings", "audit_log", "sync_runs"]
    return [t for t in tables if h.one(f"SELECT count(*) AS n FROM {t} t WHERE t::text LIKE %s", (f"%{secret}%",))["n"]]


def test_get_connection_before_configuration_is_not_configured(h):
    response = h.api.get("/config/elk")
    assert_conforms(response, "getElkConnection")
    assert response.status_code == 409 and response.json()["error"]["code"] == "NOT_CONFIGURED"


def test_connection_test_reports_cluster_privileges_fields_and_sources(h):
    response = h.api.post("/config/elk/test", json=connection_input())
    assert_conforms(response, "testElkConnection")
    result = response.json()
    assert result["ok"] is True and result["error"] is None
    assert result["authenticated"] == {"api_key_id": "fake-key-id", "api_key_name": "ir-desktop-test", "username": "tester"}
    assert result["cluster"] == {"cluster_key": "fake-cluster-uuid", "uuid": "fake-cluster-uuid", "name": "lab-cluster", "version": "9.5.5"}
    assert {p["pattern"] for p in result["privileges"]} == {"logs-windows.sysmon_operational-*", ".alerts-security.alerts-default"}
    assert {c["pattern"]: c["count"] for c in result["counts"]} == {
        "logs-windows.sysmon_operational-*": 18,
        ".alerts-security.alerts-default": 2,
    }
    assert all(f["present"] for f in result["fields"])
    assert [(s["name"], s["timestamp_strategy"], s["timestamp_field"]) for s in result["suggested_sources"]] == [
        ("sysmon", "INGESTED", "event.ingested"),
        ("security-alerts", "ALERT", "@timestamp"),
    ]
    assert API_KEY not in response.text
    assert h.one("SELECT count(*) AS n FROM elk_connections")["n"] == 0


def test_connection_test_errors(h, fake):
    bad_key = h.api.post("/config/elk/test", json=connection_input(api_key="wrong"))
    assert_conforms(bad_key, "testElkConnection")
    assert bad_key.status_code == 502 and bad_key.json()["error"]["code"] == "ELK_AUTH_FAILED"

    no_ca = h.api.post("/config/elk/test", json={"url": "https://elk.lab.test:9200", "api_key": API_KEY})
    assert_conforms(no_ca, "testElkConnection")
    assert no_ca.status_code == 422

    plain_http = h.api.post("/config/elk/test", json=connection_input(url="http://elk.lab.test:9200"))
    assert_conforms(plain_http, "testElkConnection")
    assert plain_http.status_code == 422 and API_KEY not in plain_http.text

    missing_url = h.api.post("/config/elk/test", json={"api_key": API_KEY, "ca_fingerprint": "ab" * 32})
    assert missing_url.status_code == 422 and API_KEY not in missing_url.text

    fake.state.readable = ["logs-*"]
    forbidden = h.api.post("/config/elk/test", json=connection_input())
    assert_conforms(forbidden, "testElkConnection")
    result = forbidden.json()
    assert result["ok"] is False and result["error"]["code"] == "ELK_FORBIDDEN"
    assert [p["pattern"] for p in result["privileges"] if not p["read"]] == [".alerts-security.alerts-default"]


def test_connection_test_without_monitor_needs_cluster_name(dsn):
    with FakeElasticsearch(FakeState(monitor=False)) as server:
        server.load_ndjson(FIXTURES)
        h = Harness(dsn, server, Clock(NOW))
        try:
            anonymous = h.api.post("/config/elk/test", json=connection_input()).json()
            assert anonymous["ok"] is False and anonymous["error"]["code"] == "VALIDATION_ERROR"
            named = h.api.post("/config/elk/test", json=connection_input(cluster_name="Lab Cluster"))
            assert_conforms(named, "testElkConnection")
            assert named.json()["ok"] is True and named.json()["cluster"]["cluster_key"] == "name:lab-cluster"
        finally:
            h.close()


def test_save_get_and_delete_connection_keep_key_out_of_database(h):
    saved = h.api.put("/config/elk", json=connection_input())
    assert_conforms(saved, "saveElkConnection")
    body = saved.json()
    assert body["state"] == "ACTIVE" and body["cluster_key"] == "fake-cluster-uuid" and "api_key" not in body
    assert h.store.get(elk_ref("fake-cluster-uuid")) == API_KEY
    assert secrets_in_database(h, API_KEY) == []

    current = h.api.get("/config/elk")
    assert_conforms(current, "getElkConnection")
    assert current.json() == body

    sources = h.api.get("/config/sources")
    assert_conforms(sources, "listSources")
    assert [(s["name"], s["kind"], s["state"]) for s in sources.json()] == [
        ("sysmon", "log", "ACTIVE"),
        ("security-alerts", "alert", "ACTIVE"),
    ]

    audit = h.rows("SELECT action, detail FROM audit_log ORDER BY id")
    assert audit[-1]["action"] == "config.elk.save" and audit[-1]["detail"]["api_key_name"] == "ir-desktop-test"

    deleted = h.api.delete("/config/elk")
    assert_conforms(deleted, "deleteElkConnection")
    assert deleted.status_code == 204 and h.store.get(elk_ref("fake-cluster-uuid")) is None
    assert h.api.get("/config/elk").status_code == 409
    assert h.api.get("/config/sources").json() == []
    assert h.one("SELECT count(*) AS n FROM source_configs")["n"] == 2
    assert h.api.delete("/config/elk").status_code == 204


def test_save_rejects_bad_key_and_missing_privileges(h, fake):
    bad = h.api.put("/config/elk", json=connection_input(api_key="wrong"))
    assert_conforms(bad, "saveElkConnection")
    assert bad.status_code == 502 and bad.json()["error"]["code"] == "ELK_AUTH_FAILED"
    fake.state.readable = ["logs-*"]
    forbidden = h.api.put("/config/elk", json=connection_input())
    assert_conforms(forbidden, "saveElkConnection")
    assert forbidden.json()["error"]["code"] == "ELK_FORBIDDEN"
    assert forbidden.json()["error"]["detail"]["patterns"] == [".alerts-security.alerts-default"]
    invalid = h.api.put("/config/elk", json=connection_input(url="ftp://x"))
    assert_conforms(invalid, "saveElkConnection")
    assert h.one("SELECT count(*) AS n FROM elk_connections")["n"] == 0 and h.store.get(elk_ref("fake-cluster-uuid")) is None


def test_new_key_for_same_cluster_reuses_checkpoints_other_cluster_starts_fresh(h, fake):
    first = h.configure()
    sysmon = h.source_id("sysmon")
    h.execute("INSERT INTO sync_checkpoints (source_id, ingest_watermark) VALUES (%s, '2026-10-30T03:00:00Z')", (sysmon,))

    fake.state.api_key, fake.state.api_key_id = "rotated-key", "rotated-id"
    second = h.configure(api_key="rotated-key")
    assert second["id"] == first["id"] and second["api_key_id"] == "rotated-id"
    assert h.checkpoint(sysmon)["ingest_watermark"] == datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)
    assert h.rows("SELECT detail FROM audit_log WHERE action = 'config.elk.save' ORDER BY id")[-1]["detail"]["key_changed"] is True

    fake.state.cluster_uuid = "other-cluster"
    third = h.configure(api_key="rotated-key")
    assert third["id"] != first["id"] and third["cluster_key"] == "other-cluster"
    assert h.source_id("sysmon") != sysmon
    assert h.store.get(elk_ref("fake-cluster-uuid")) is None and h.store.get(elk_ref("other-cluster")) == "rotated-key"


def test_replace_sources(h):
    not_configured = h.api.put("/config/sources", json=[])
    assert_conforms(not_configured, "replaceSources")
    assert not_configured.status_code == 409

    h.configure()
    sysmon = h.source_id("sysmon")
    h.execute("INSERT INTO sync_checkpoints (source_id, ingest_watermark) VALUES (%s, now())", (sysmon,))
    h.execute("UPDATE source_configs SET state = 'ERROR' WHERE id = %s", (sysmon,))
    spec = {
        "name": "sysmon",
        "kind": "log",
        "index_patterns": ["logs-windows.sysmon_operational-*"],
        "timestamp_strategy": "EVENT_TIME",
        "timestamp_field": "@timestamp",
        "overlap_s": 900,
    }
    replaced = h.api.put("/config/sources", json=[spec])
    assert_conforms(replaced, "replaceSources")
    by_name = {s["name"]: s for s in replaced.json()}
    assert by_name["sysmon"]["timestamp_field"] == "@timestamp" and by_name["sysmon"]["batch_size"] == 1000
    assert by_name["sysmon"]["state"] == "ACTIVE"
    assert by_name["security-alerts"]["enabled"] is False
    assert h.checkpoint(sysmon)["ingest_watermark"] is None

    duplicate = h.api.put("/config/sources", json=[spec, spec])
    assert_conforms(duplicate, "replaceSources")
    assert duplicate.status_code == 422
    bad = h.api.put("/config/sources", json=[{**spec, "index_patterns": []}])
    assert_conforms(bad, "replaceSources")
    assert bad.status_code == 422


def test_settings_round_trip_and_validation(h):
    current = h.api.get("/config/settings")
    assert_conforms(current, "getSettings")
    assert current.json() == {
        "sync_interval_s": 300,
        "quota_bytes": 32212254720,
        "retention_days": 90,
        "reconcile_window_h": 24,
        "alert_resync_days": 7,
    }
    updated = h.api.put("/config/settings", json={**current.json(), "sync_interval_s": 600, "retention_days": 30})
    assert_conforms(updated, "updateSettings")
    assert updated.json()["sync_interval_s"] == 600 and h.api.get("/config/settings").json()["retention_days"] == 30
    assert h.rows("SELECT detail FROM audit_log WHERE action = 'config.settings.update'")[0]["detail"] == {
        "sync_interval_s": {"from": 300, "to": 600},
        "retention_days": {"from": 90, "to": 30},
    }
    invalid = h.api.put("/config/settings", json={**current.json(), "sync_interval_s": 10})
    assert_conforms(invalid, "updateSettings")
    assert invalid.status_code == 422


def test_endpoints_require_session_token(h):
    response = h.api.get("/config/elk", headers={"Authorization": "Bearer wrong"})
    assert_conforms(response, "getElkConnection")
    assert response.status_code == 401
