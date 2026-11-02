from datetime import datetime, timezone

import psycopg
import pytest
from elasticsearch import ApiError
from psycopg.rows import dict_row

from ir_engine.contracts import ErrorCode, SourceKind
from ir_engine.elk import queries
from ir_engine.elk.client import make_client
from ir_engine.elk.errors import classify_exception
from ir_engine.fixtures import ensure_fixture_source
from ir_engine.ingest.writer import write_hits
from ir_engine.testing.fake_es import FakeElasticsearch, FakeState
from ir_engine.tools.lab_check import run_checks

from .conftest import FIXTURES

LOGS = ["logs-windows.sysmon_operational-*"]
ALERTS = [".alerts-security.alerts-default"]
FROM = datetime(2026, 10, 30, tzinfo=timezone.utc)
TO = datetime(2026, 10, 31, tzinfo=timezone.utc)


@pytest.fixture
def fake():
    with FakeElasticsearch() as server:
        server.load_ndjson(FIXTURES)
        yield server


def client(fake, key="fake-api-key"):
    return make_client(fake.url, key, max_retries=0)


def fetch_window(es, time_field, lower, upper, size):
    pit = es.open_point_in_time(index=",".join(LOGS), **queries.open_pit_params())["id"]
    hits, after, pages = [], None, 0
    try:
        while True:
            resp = es.search(**queries.window_search(pit, time_field, lower, upper, size, after))
            assert not queries.is_partial(resp.body)
            batch = resp["hits"]["hits"]
            if not batch:
                break
            hits.extend(batch)
            after = batch[-1]["sort"]
            pit = resp["pit_id"]
            pages += 1
    finally:
        es.close_point_in_time(id=pit)
    return hits, pages


def test_wizard_calls(fake):
    es = client(fake)
    assert es.info()["version"]["number"].startswith("9.")
    assert es.security.authenticate()["api_key"]["name"] == "ir-desktop-test"
    priv = es.security.has_privileges(index=[{"names": LOGS + ALERTS, "privileges": queries.LOG_PRIVILEGES}])
    assert priv["has_all_requested"] is True
    caps = es.field_caps(index=LOGS[0], fields=",".join(queries.LOG_REQUIRED_FIELDS))
    assert set(caps["fields"]) == set(queries.LOG_REQUIRED_FIELDS)


def test_pit_pagination_feeds_writer(fake, dsn):
    es = client(fake)
    hits, pages = fetch_window(es, "event.ingested", FROM, TO, size=4)
    assert len(hits) == 18 and pages == 5
    assert [h["sort"] for h in hits] == sorted(h["sort"] for h in hits)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        source = ensure_fixture_source(conn, "sysmon", SourceKind.LOG)
        conn.commit()
        stats = write_hits(conn, source, hits)
    assert (stats.inserted, stats.duplicates) == (15, 3)


def test_window_bounds_are_respected(fake):
    es = client(fake)
    lower = datetime(2026, 10, 30, 2, 15, tzinfo=timezone.utc)
    upper = datetime(2026, 10, 30, 2, 16, tzinfo=timezone.utc)
    hits, _ = fetch_window(es, "event.ingested", lower, upper, size=100)
    assert hits and all(lower.timestamp() * 1000 <= h["sort"][0] < upper.timestamp() * 1000 for h in hits)


def test_auth_and_rate_limit_errors_are_classified(fake):
    with pytest.raises(ApiError) as bad_key:
        client(fake, key="wrong").security.authenticate()
    assert classify_exception(bad_key.value) is ErrorCode.ELK_AUTH_FAILED
    fake.state.fail_next(429)
    with pytest.raises(ApiError) as limited:
        client(fake).count(index=LOGS[0])
    assert classify_exception(limited.value) is ErrorCode.ELK_RATE_LIMITED


def test_partial_shards_are_detected(fake):
    fake.state.partial = True
    es = client(fake)
    pit = es.open_point_in_time(index=LOGS[0], **queries.open_pit_params())["id"]
    resp = es.search(**queries.window_search(pit, "event.ingested", FROM, TO, 10))
    assert queries.is_partial(resp.body)


def test_lab_check_passes_and_detects_missing_privilege():
    with FakeElasticsearch() as server:
        server.load_ndjson(FIXTURES)
        checks = {c.name: c for c in run_checks(client(server), LOGS, ALERTS)}
        assert all(c.ok for c in checks.values()), checks
    with FakeElasticsearch(FakeState(readable=["logs-*"], monitor=False)) as server:
        server.load_ndjson(FIXTURES)
        checks = {c.name: c for c in run_checks(client(server), LOGS, ALERTS)}
        assert not checks["index_privileges"].ok
        assert checks["elasticsearch_9x"].ok and not checks["elasticsearch_9x"].required
        assert not checks["alerts_present"].ok
