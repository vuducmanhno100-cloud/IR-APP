import importlib.util
from collections import Counter
from datetime import datetime, timezone

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from ir_engine import audit, fixtures
from ir_engine.elk.client import make_client
from ir_engine.testing.contract import assert_conforms, engine_client, operations
from ir_engine.testing.fake_es import FakeElasticsearch
from ir_engine.tools import export_hits, stub_report

from .conftest import FIXTURES

FROM = datetime(2026, 10, 30, tzinfo=timezone.utc)
TO = datetime(2026, 10, 31, tzinfo=timezone.utc)


def test_assert_conforms_accepts_real_engine_response(dsn):
    with engine_client(dsn) as api:
        assert_conforms(api.get("/system/health"), "getHealth")
        assert_conforms(api.get("/system/health", headers={"Authorization": "Bearer wrong"}), "getHealth")


def test_assert_conforms_rejects_stub_and_wrong_shapes(dsn):
    with engine_client(dsn) as api:
        with pytest.raises(AssertionError, match="501"):
            assert_conforms(api.get("/sync/status"), "getSyncStatus")
        with pytest.raises(AssertionError, match="getSyncStatus is GET /sync/status"):
            assert_conforms(api.get("/system/health"), "getSyncStatus")
    request = httpx.Request("GET", "http://engine/system/health")
    with pytest.raises(AssertionError, match="engine_version"):
        assert_conforms(httpx.Response(200, json={"status": "ok"}, request=request), "getHealth")


def test_stub_report_lists_every_operation():
    rows = stub_report.operation_status()
    assert {r["operation"] for r in rows} == set(operations())
    pending = Counter(r["owner"] for r in rows if not r["done"])
    assert pending == {"TV1": 16, "TV2": 13}
    assert {r["operation"] for r in rows if r["done"]} == {"getHealth", "shutdown"}
    assert stub_report.main(["--strict", "--owner", "TV2"]) == 1


def test_export_hits_feeds_fixture_loader(dsn, tmp_path):
    with FakeElasticsearch() as server:
        server.load_ndjson(FIXTURES)
        es = make_client(server.url, "fake-api-key", max_retries=0)
        hits = export_hits.export_hits(es, export_hits.DEFAULT_PATTERNS["log"], "event.ingested", FROM, TO, limit=100, page_size=4)
        assert len(hits) == 18 and all(set(h) == {"_index", "_id", "_source"} for h in hits)
        assert len(export_hits.export_hits(es, export_hits.DEFAULT_PATTERNS["log"], "event.ingested", FROM, TO, limit=5)) == 5
    out = tmp_path / "lab" / "logs" / "sample.ndjson"
    export_hits.write_ndjson(hits, out)
    totals = fixtures.load(dsn, [out.parent])
    assert (totals["inserted"], totals["duplicates"]) == (15, 3)


def test_audit_record_rejects_secrets(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        audit_id = audit.record(conn, "config.elk.save", "elk_connections:1", {"url": "https://elk.lab:9200"})
        row = conn.execute("SELECT actor, action, target, detail FROM audit_log WHERE id = %s", (audit_id,)).fetchone()
        assert row == {
            "actor": "analyst",
            "action": "config.elk.save",
            "target": "elk_connections:1",
            "detail": {"url": "https://elk.lab:9200"},
        }
        with pytest.raises(ValueError):
            audit.record(conn, "config.elk.save", detail={"connection": {"api_key": "x"}})


def test_perf_generator_feeds_writer(dsn):
    spec = importlib.util.spec_from_file_location("generate", FIXTURES / "generate.py")
    generate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generate)
    docs = generate.perf(generate.Recorder(), 500)
    assert len(docs) == 500 and len({d["_id"] for d in docs}) == 500
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        source = fixtures.ensure_fixture_source(conn, "perf", fixtures.SourceKind.LOG)
        conn.commit()
        stats = fixtures.write_hits(conn, source, docs)
    assert stats.inserted == 500


def test_retention_deletes_raw_events_without_tv2_code(dsn):
    fixtures.load(dsn, [FIXTURES / "01_normal_tree.ndjson"])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        event = conn.execute("SELECT id, host_id, event_time FROM raw_events ORDER BY id LIMIT 1").fetchone()
        proc = conn.execute(
            "INSERT INTO process_instances (host_id, process_guid, create_event_id) VALUES (%s, 'g', %s) RETURNING id",
            (event["host_id"], event["id"]),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO event_process_links (event_id, process_id, link_type, level, evidence_rule) VALUES (%s, %s, 'create', 'VERIFIED', 'r')",
            (event["id"], proc),
        )
        conn.execute(
            "INSERT INTO event_search_terms (event_id, term_type, term_value, event_time) VALUES (%s, 'image', 'x.exe', %s)",
            (event["id"], event["event_time"]),
        )
        conn.execute("DELETE FROM raw_events WHERE id = %s", (event["id"],))
        assert conn.execute("SELECT create_event_id FROM process_instances WHERE id = %s", (proc,)).fetchone()["create_event_id"] is None
        assert conn.execute("SELECT count(*) AS n FROM event_process_links WHERE event_id = %s", (event["id"],)).fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM event_search_terms WHERE event_id = %s", (event["id"],)).fetchone()["n"] == 0
