"""Regressions found by the FE/BE audit: search input edge cases, tree completeness and limits, alerts, export."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from ir_engine.correlation.worker import run_once
from ir_engine.fixtures import load
from ir_engine.investigation import service
from ir_engine.investigation.service import trace
from ir_engine.testing.contract import assert_conforms, engine_client

from .conftest import FIXTURES


def loaded(dsn):
    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        run_once(conn)
        return conn.execute("SELECT host_name FROM hosts ORDER BY id LIMIT 1").fetchone()["host_name"]


def test_search_rejects_values_that_normalize_to_nothing(dsn):
    loaded(dsn)
    with engine_client(dsn) as client:
        for term, value in [("process_guid", "{}"), ("domain", "."), ("ip", "  ")]:
            response = client.get("/search/events", params={"type": term, "value": value, "match": "prefix"})
            assert_conforms(response, "searchEvents")
            assert response.status_code == 422, (term, value)


def test_host_filter_ignores_case(dsn):
    host = loaded(dsn)
    with engine_client(dsn) as client:
        totals = []
        for name in (host, host.upper(), host.lower()):
            response = client.get("/search/events", params={"type": "user", "value": "analyst", "host": name})
            assert_conforms(response, "searchEvents")
            totals.append(response.json()["total"])
    assert totals[0] > 0 and len(set(totals)) == 1


def first_event_with_process(conn):
    return conn.execute("SELECT event_id, process_id FROM event_process_links ORDER BY event_id LIMIT 1").fetchone()


def test_pending_events_outside_the_tree_lifetime_do_not_mark_it_pending(dsn):
    """Any PENDING event on the host used to mark every tree there PENDING_DATA while a sync was running."""
    loaded(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        host = conn.execute(
            "INSERT INTO hosts(host_key,host_name,first_seen,last_seen) VALUES ('life',  'life',now(),now()) RETURNING id"
        ).fetchone()["id"]
        start = datetime(2026, 10, 30, 1, 0, tzinfo=timezone.utc)

        def process(guid, began, ended):
            return conn.execute(
                "INSERT INTO process_instances(host_id,process_guid,name,started_at,ended_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (host, guid, guid, began, ended),
            ).fetchone()["id"]

        parent = process("parent", start, start + timedelta(hours=1))
        child = process("child", start + timedelta(minutes=5), start + timedelta(minutes=30))
        conn.execute("INSERT INTO process_edges(parent_id,child_id,level,rule_ver) VALUES (%s,%s,'VERIFIED','test')", (parent, child))

        def pending_at(moment):
            conn.execute(
                "INSERT INTO raw_events (source_id,cluster_key,index_name,elastic_id,host_id,event_code,event_time,payload)"
                " SELECT source_id,cluster_key,index_name,md5(random()::text),%s,'1',%s,'{}'::jsonb FROM raw_events LIMIT 1",
                (host, moment),
            )

        assert trace(conn, process_id=parent)["completeness"] == "COMPLETE"
        pending_at(start - timedelta(days=1))
        pending_at(start + timedelta(hours=3))
        assert trace(conn, process_id=parent)["completeness"] == "COMPLETE"
        pending_at(start + timedelta(minutes=10))
        assert trace(conn, process_id=parent)["completeness"] == "PENDING_DATA"


def test_tree_says_when_related_events_were_cut(dsn, monkeypatch):
    loaded(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        link = first_event_with_process(conn)
        whole = trace(conn, event_id=link["event_id"])
        assert len(whole["warnings"]) == 1 and whole["warnings"][0] == service.COVERAGE_WARNING
        monkeypatch.setattr(service, "RELATED_LIMIT", 1)
        cut = trace(conn, event_id=link["event_id"])
    assert len(whole["related_events"]) > 1 and len(cut["related_events"]) == 1
    assert any("event liên quan" in w for w in cut["warnings"])
    assert [n["event_counts"] for n in cut["nodes"]] == [n["event_counts"] for n in whole["nodes"]]


def test_alerts_link_their_original_events(dsn):
    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    load(dsn, [FIXTURES / "alerts"], source_name="security-alerts", kind="alert")
    with engine_client(dsn) as client:
        response = client.get("/alerts", params={"limit": 50})
        assert_conforms(response, "listAlerts")
        items = response.json()["items"]
    assert items and any(item["original_event_id"] for item in items)


def test_export_is_a_readable_report_with_escaped_values(dsn, tmp_path):
    loaded(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        link = first_event_with_process(conn)
    with engine_client(dsn) as client:
        client.app.state.data_dir = tmp_path
        case = client.post("/cases", json={"title": "Case <script>alert(1)</script>"}).json()
        snapshot = client.get(f"/trace/event/{link['event_id']}").json()
        for item in (
            {"item_type": "event", "ref_id": link["event_id"], "note": "Lệnh <b>đáng ngờ</b>"},
            {"item_type": "process", "ref_id": link["process_id"]},
            {"item_type": "tree_snapshot", "snapshot": snapshot},
        ):
            assert client.post(f"/cases/{case['id']}/items", json=item).status_code == 201
        exported = client.post(f"/cases/{case['id']}/export", json={"format": "html"})
        assert_conforms(exported, "exportCase")
        report = Path(exported.json()["artifact_path"]).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in report and "&lt;script&gt;" in report
    assert "Lệnh &lt;b&gt;đáng ngờ&lt;/b&gt;" in report
    assert report.count("<section class=item>") == 3
    assert "Command line" in report and "ul class=tree" in report and "Dữ liệu đầy đủ" in report
