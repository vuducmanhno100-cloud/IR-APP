import threading
import time
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from ir_engine.contracts import RunType
from ir_engine.sync import config, runner
from ir_engine.sync.scheduler import SyncScheduler
from ir_engine.testing.contract import assert_conforms
from ir_engine.testing.fake_es import FakeElasticsearch

from .conftest import FIXTURES
from .tv1_support import API_KEY, DAY_START, ES_URL, FINGERPRINT, NOW, Clock, Harness, fake_factory, iso, log_hit


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


def bootstrap(h: Harness, lower=DAY_START, upper=NOW, **extra):
    return h.api.post("/sync/bootstrap", json={"from": iso(lower), "to": iso(upper), **extra})


def synced(h: Harness) -> dict:
    h.configure()
    assert bootstrap(h).status_code == 202
    h.tick_until_idle()
    return {"sysmon": h.source_id("sysmon"), "alerts": h.source_id("security-alerts")}


def test_bootstrap_loads_history_once_and_hands_over_to_incremental(h):
    h.configure()
    response = bootstrap(h)
    assert_conforms(response, "startBootstrap")
    queued = response.json()["runs"]
    assert [(r["run_type"], r["status"], r["lower_bound"], r["upper_bound"]) for r in queued] == [
        ("BOOTSTRAP", "QUEUED", "2026-10-30T00:00:00Z", "2026-10-30T01:00:00Z"),
        ("BOOTSTRAP", "QUEUED", "2026-10-30T00:00:00Z", "2026-10-30T04:00:00Z"),
    ]
    h.tick_until_idle()

    sysmon, alerts = h.source_id("sysmon"), h.source_id("security-alerts")
    log_chunks = [r for r in h.runs(sysmon) if r["run_type"] == "BOOTSTRAP"]
    assert len(log_chunks) == 4 and {r["status"] for r in log_chunks} == {"SUCCEEDED"}
    assert sum(r["duplicate_skipped"] for r in log_chunks) == 3
    assert h.one("SELECT count(*) AS n FROM raw_events WHERE source_id = %s", (sysmon,))["n"] == 15
    assert h.one("SELECT status FROM alert_records")["status"] == "acknowledged"
    assert config.plan_key(sysmon) not in {r["key"] for r in h.rows("SELECT key FROM app_settings")}
    assert h.checkpoint(sysmon)["ingest_watermark"] == NOW
    assert h.rows("SELECT index_name, doc_count FROM sync_run_indices WHERE run_id = %s", (log_chunks[2]["id"],)) == [
        {"index_name": ".ds-logs-windows.sysmon_operational-default-2026.10.30-000001", "doc_count": 16}
    ]

    status = h.api.get("/sync/status")
    assert_conforms(status, "getSyncStatus")
    by_name = {s["name"]: s for s in status.json()["sources"]}
    assert by_name["sysmon"]["pending_events"] == 15 and by_name["sysmon"]["failed_events"] == 0
    assert by_name["sysmon"]["process_watermark"] == "2026-10-30T02:00:03Z"
    assert by_name["sysmon"]["ingest_watermark"] == "2026-10-30T04:00:00Z" and by_name["sysmon"]["lag_s"] == 0
    assert by_name["security-alerts"]["process_watermark"] == by_name["security-alerts"]["ingest_watermark"]
    assert h.checkpoint(alerts)["alert_status_watermark"] == NOW - timedelta(seconds=60)

    h.fake.state.add_hits([log_hit(h.fake, 5001, NOW + timedelta(minutes=3))])
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    incremental = [r for r in h.runs(sysmon) if r["run_type"] == "INCREMENTAL"][-1]
    assert (incremental["status"], incremental["fetched_count"], incremental["upserted_count"]) == ("SUCCEEDED", 1, 1)
    assert incremental["lower_bound"] == NOW - timedelta(seconds=120)
    assert h.checkpoint(sysmon)["ingest_watermark"] == NOW + timedelta(minutes=9)


def test_incremental_overlap_rereads_without_duplicates(h):
    ids = synced(h)
    h.fake.state.add_hits([log_hit(h.fake, 6001, NOW + timedelta(minutes=8, seconds=30))])
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    h.clock.advance(minutes=5)
    h.tick_until_idle()
    last = [r for r in h.runs(ids["sysmon"]) if r["run_type"] == "INCREMENTAL"][-1]
    assert last["fetched_count"] == 1 and last["duplicate_skipped"] == 1 and last["upserted_count"] == 0
    assert h.one("SELECT count(*) AS n FROM raw_events WHERE source_id = %s", (ids["sysmon"],))["n"] == 16


def test_revoked_key_stops_sync_and_new_key_resumes_from_checkpoint(h):
    ids = synced(h)
    watermark = h.checkpoint(ids["sysmon"])["ingest_watermark"]
    h.fake.state.api_key = "rotated"
    h.clock.advance(minutes=10)
    h.tick_until_idle()

    failed = h.runs(ids["sysmon"])[-1]
    assert (failed["status"], failed["error_code"]) == ("FAILED", "ELK_AUTH_FAILED")
    assert h.checkpoint(ids["sysmon"])["ingest_watermark"] == watermark
    assert h.api.get("/config/elk").json()["state"] == "AUTH_FAILED"
    status = h.api.get("/sync/status")
    assert_conforms(status, "getSyncStatus")
    assert status.json()["scheduler"] == "blocked"
    assert {s["state"] for s in status.json()["sources"]} == {"AUTH_FAILED"}
    assert status.json()["sources"][0]["last_error"]["code"] == "ELK_AUTH_FAILED"
    runs_before = len(h.runs())
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    assert len(h.runs()) == runs_before

    blocked = h.api.post("/sync/run-now", json={})
    assert_conforms(blocked, "runNow")
    assert blocked.status_code == 409

    h.configure(api_key="rotated")
    h.tick_until_idle()
    assert h.runs(ids["sysmon"])[-1]["status"] == "SUCCEEDED"
    assert h.checkpoint(ids["sysmon"])["ingest_watermark"] > watermark


def test_missing_privilege_marks_source_forbidden_until_restored(h):
    ids = synced(h)
    h.fake.state.readable = [".alerts-*", ".internal.alerts-*"]
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    failed = h.runs(ids["sysmon"])[-1]
    assert (failed["status"], failed["error_code"]) == ("FAILED", "ELK_FORBIDDEN")
    assert "logs-windows.sysmon_operational-*" in failed["error"]
    assert h.one("SELECT state FROM source_configs WHERE id = %s", (ids["sysmon"],))["state"] == "FORBIDDEN"
    assert h.one("SELECT state FROM source_configs WHERE id = %s", (ids["alerts"],))["state"] == "ACTIVE"

    h.fake.state.readable = ["*"]
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    assert h.one("SELECT state FROM source_configs WHERE id = %s", (ids["sysmon"],))["state"] == "ACTIVE"
    assert h.runs(ids["sysmon"])[-1]["status"] == "SUCCEEDED"


def test_narrowed_key_is_reported_even_without_403(h):
    h.fake.state.add_hits([log_hit(h.fake, 7001, NOW - timedelta(hours=1), namespace="lab2")])
    ids = synced(h)
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    assert h.runs(ids["sysmon"])[-1]["error_code"] is None

    h.fake.state.readable = ["logs-windows.sysmon_operational-default", ".alerts-*", ".internal.alerts-*"]
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    warned = [r for r in h.runs(ids["sysmon"]) if r["run_type"] == "INCREMENTAL"][-1]
    assert (warned["status"], warned["error_code"]) == ("SUCCEEDED", "SYNC_PARTIAL")
    assert "logs-windows.sysmon_operational-lab2" in warned["error"]
    sysmon_status = next(s for s in h.api.get("/sync/status").json()["sources"] if s["name"] == "sysmon")
    assert sysmon_status["last_error"]["code"] == "SYNC_PARTIAL"


def test_rate_limit_backs_off_and_partial_results_keep_watermark(h):
    ids = synced(h)
    watermark = h.checkpoint(ids["sysmon"])["ingest_watermark"]
    h.clock.advance(minutes=10)
    h.fake.state.fail_next(429)
    h.tick_until_idle()
    limited = h.runs(ids["sysmon"])[-1]
    assert (limited["status"], limited["error_code"]) == ("FAILED", "ELK_RATE_LIMITED")
    assert h.one("SELECT state FROM source_configs WHERE id = %s", (ids["sysmon"],))["state"] == "ACTIVE"

    count = len(h.runs(ids["sysmon"]))
    h.tick_until_idle()
    assert len(h.runs(ids["sysmon"])) == count

    h.fake.state.partial = True
    h.clock.advance(seconds=31)
    h.tick_until_idle()
    partial = h.runs(ids["sysmon"])[-1]
    assert (partial["status"], partial["error_code"]) == ("PARTIAL", "SYNC_PARTIAL")
    assert h.checkpoint(ids["sysmon"])["ingest_watermark"] == watermark

    h.fake.state.partial = False
    h.clock.advance(minutes=2)
    h.tick_until_idle()
    assert h.runs(ids["sysmon"])[-1]["status"] == "SUCCEEDED"
    assert h.checkpoint(ids["sysmon"])["ingest_watermark"] > watermark


def test_alert_status_change_is_resynced(dsn):
    with FakeElasticsearch() as server:
        server.load_ndjson(FIXTURES / "01_normal_tree.ndjson")
        server.load_ndjson(FIXTURES / "alerts")
        server.state.docs = [d for d in server.state.docs if d["_source"].get("kibana.alert.workflow_status") != "acknowledged"]
        h = Harness(dsn, server, Clock(NOW))
        try:
            synced(h)
            assert h.one("SELECT status FROM alert_records")["status"] == "open"
            alert = next(d for d in server.state.docs if "kibana.alert.uuid" in d["_source"])
            alert["_source"]["kibana.alert.workflow_status"] = "closed"
            alert["_source"]["kibana.alert.workflow_status_updated_at"] = iso(NOW + timedelta(minutes=2))
            h.clock.advance(minutes=10)
            h.tick_until_idle()
            assert h.one("SELECT status, status_updated_at FROM alert_records") == {
                "status": "closed",
                "status_updated_at": NOW + timedelta(minutes=2),
            }
            resync = [r for r in h.runs(h.source_id("security-alerts")) if r["run_type"] == "ALERT_RESYNC"][-1]
            assert resync["status"] == "SUCCEEDED" and resync["fetched_count"] == 1
        finally:
            h.close()


def test_run_now_retry_and_conflicts(h):
    h.configure()
    queued = h.api.post("/sync/run-now", json={})
    assert_conforms(queued, "runNow")
    runs = queued.json()["runs"]
    assert {r["run_type"] for r in runs} == {"INCREMENTAL"} and len(runs) == 2

    again = h.api.post("/sync/run-now")
    assert_conforms(again, "runNow")
    assert again.status_code == 409 and again.json()["error"]["code"] == "SYNC_BUSY"

    h.fake.state.fail_next(429)
    h.tick_until_idle()
    failed = next(r for r in h.runs() if r["status"] == "FAILED")

    not_found = h.api.post("/sync/retry/999999")
    assert_conforms(not_found, "retryRun")
    assert not_found.status_code == 404

    retried = h.api.post(f"/sync/retry/{failed['id']}")
    assert_conforms(retried, "retryRun")
    assert retried.json()["status"] == "QUEUED" and retried.json()["lower_bound"] == iso(failed["lower_bound"])
    twice = h.api.post(f"/sync/retry/{failed['id']}")
    assert_conforms(twice, "retryRun")
    assert twice.status_code == 409

    h.tick_until_idle()
    done = h.one("SELECT status FROM sync_runs WHERE id = %s", (retried.json()["id"],))
    assert done["status"] == "SUCCEEDED"
    succeeded = h.api.post(f"/sync/retry/{retried.json()['id']}")
    assert_conforms(succeeded, "retryRun")
    assert succeeded.status_code == 409


def test_bootstrap_validation_and_conflicts(h):
    not_configured = bootstrap(h)
    assert_conforms(not_configured, "startBootstrap")
    assert not_configured.status_code == 409 and not_configured.json()["error"]["code"] == "NOT_CONFIGURED"

    h.configure()
    for lower, upper in [(NOW, DAY_START), (DAY_START, NOW + timedelta(days=1)), (NOW - timedelta(days=200), NOW)]:
        response = bootstrap(h, lower, upper)
        assert_conforms(response, "startBootstrap")
        assert response.status_code == 422
    naive = h.api.post("/sync/bootstrap", json={"from": "2026-10-30T00:00:00", "to": "2026-10-30T04:00:00"})
    assert_conforms(naive, "startBootstrap")
    assert naive.status_code == 422
    unknown = bootstrap(h, source_ids=[424242])
    assert unknown.status_code == 422

    assert bootstrap(h, source_ids=[h.source_id("sysmon")]).status_code == 202
    conflict = bootstrap(h)
    assert_conforms(conflict, "startBootstrap")
    assert conflict.status_code == 409 and conflict.json()["error"]["detail"]["source_ids"] == [h.source_id("sysmon")]


def test_pause_resume_and_interrupted_runs(h):
    ids = synced(h)
    paused = h.api.post("/sync/pause")
    assert_conforms(paused, "pauseSync")
    assert paused.status_code == 202 and h.scheduler.should_interrupt()
    count = len(h.runs())
    h.clock.advance(minutes=10)
    assert h.scheduler.tick() is False and len(h.runs()) == count

    restarted = SyncScheduler(h.db, store=h.store, client_factory=fake_factory(h.fake), clock=h.clock)
    restarted.start()
    try:
        assert restarted.state == "paused"
    finally:
        restarted.stop()

    resumed = h.api.post("/sync/resume")
    assert_conforms(resumed, "resumeSync")
    assert not h.scheduler.should_interrupt()
    h.tick_until_idle()
    assert len(h.runs()) > count

    with h.db.connection() as conn:
        source = config.get_source(conn, ids["sysmon"])
        run = runner.create_run(conn, ids["sysmon"], RunType.INCREMENTAL, NOW, NOW + timedelta(minutes=1))
        conn.commit()
        es = fake_factory(h.fake)(ES_URL, API_KEY, ca_fingerprint=FINGERPRINT)
        outcome = runner.execute_run(conn, es, dict(source), "fake-cluster-uuid", run, h.clock, lambda: True)
        es.close()
    assert outcome.status == "CANCELLED"


def test_stale_running_runs_are_closed_on_startup(h):
    ids = synced(h)
    h.execute(
        "INSERT INTO sync_runs (source_id, run_type, status, lower_bound, upper_bound, started_at) "
        "VALUES (%s, 'INCREMENTAL', 'FETCHING', %s, %s, %s)",
        (ids["sysmon"], NOW, NOW + timedelta(minutes=5), NOW),
    )
    fresh = SyncScheduler(h.db, store=h.store, client_factory=fake_factory(h.fake), clock=h.clock)
    h.clock.advance(minutes=10)
    while fresh.tick():
        pass
    stale = h.one("SELECT status, error_code, error FROM sync_runs WHERE run_type = 'INCREMENTAL' AND lower_bound = %s", (NOW,))
    assert stale["status"] == "CANCELLED" and stale["error_code"] is None and "Engine dừng" in stale["error"]
    assert h.runs(ids["sysmon"])[-1]["status"] == "SUCCEEDED"


def test_runs_are_listed_newest_first_with_cursor(h):
    ids = synced(h)
    first = h.api.get("/sync/runs", params={"limit": 2})
    assert_conforms(first, "listRuns")
    page = first.json()
    assert len(page["items"]) == 2 and page["items"][0]["id"] > page["items"][1]["id"] and page["next_cursor"]
    second = h.api.get("/sync/runs", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert second["items"][0]["id"] < page["items"][1]["id"]
    only = h.api.get("/sync/runs", params={"source_id": ids["alerts"], "limit": 500}).json()
    assert {r["source_id"] for r in only["items"]} == {ids["alerts"]} and only["next_cursor"] is None
    assert h.api.get("/sync/runs", params={"cursor": "%%%"}).status_code == 422


def test_reconcile_refetches_hours_where_elk_has_more(h):
    ids = synced(h)
    h.execute("DELETE FROM raw_events WHERE source_id = %s AND ingest_time < '2026-10-30T02:10:00Z'", (ids["sysmon"],))
    remaining = h.one("SELECT count(*) AS n FROM raw_events WHERE source_id = %s", (ids["sysmon"],))["n"]
    assert remaining < 15
    h.clock.advance(hours=1, minutes=1)
    h.tick_until_idle()
    reconcile = [r for r in h.runs(ids["sysmon"]) if r["run_type"] == "RECONCILE"][-1]
    assert reconcile["status"] == "SUCCEEDED" and reconcile["upserted_count"] == 15 - remaining
    assert h.one("SELECT count(*) AS n FROM raw_events WHERE source_id = %s", (ids["sysmon"],))["n"] == 15
    # 24 hours back would reach before the bootstrap; reconcile stays inside what has been synced.
    assert reconcile["lower_bound"] == DAY_START


def test_first_connection_fetches_the_24_hours_before_it(h):
    old = log_hit(h.fake, 990001, NOW - timedelta(days=2))
    h.fake.state.add_hits([old])
    h.configure()
    h.tick_until_idle()
    sysmon = h.source_id("sysmon")
    start = NOW - timedelta(seconds=60 + 300)  # safety lag + sync interval
    boot = [r for r in h.runs(sysmon) if r["run_type"] == "BOOTSTRAP"]
    assert min(r["lower_bound"] for r in boot) == start - timedelta(hours=24) and max(r["upper_bound"] for r in boot) == start
    assert h.one("SELECT count(*) AS n FROM raw_events WHERE source_id = %s", (sysmon,))["n"] == 15
    assert not h.one("SELECT 1 AS x FROM raw_events WHERE elastic_id = %s", (old["_id"],))
    assert h.one("SELECT count(*) AS n FROM app_settings WHERE key LIKE 'bootstrap.%%'")["n"] == 0

    # Later passes reconcile only what has been synced: never back before the automatic 24 hours.
    h.clock.advance(hours=1, minutes=1)
    h.tick_until_idle()
    assert all(r["lower_bound"] >= start - timedelta(hours=24) for r in h.runs(sysmon) if r["run_type"] == "RECONCILE")


def test_wizard_bootstrap_extends_the_automatic_24_hours(h):
    """Saving the connection wakes the scheduler, so the wizard's bootstrap usually lands after the first sync
    planned the automatic 24 hours; the user's range is merged with it instead of being refused."""
    old = log_hit(h.fake, 990002, NOW - timedelta(days=2))
    h.fake.state.add_hits([old])
    h.configure()
    assert h.api.post("/sync/run-now", json={}).status_code == 202
    response = bootstrap(h, NOW - timedelta(days=3), NOW)
    assert_conforms(response, "startBootstrap")
    assert response.status_code == 202 and response.json()["runs"] == []
    h.tick_until_idle(limit=200)  # 72 one-hour chunks
    sysmon = h.source_id("sysmon")
    boot = [r for r in h.runs(sysmon) if r["run_type"] == "BOOTSTRAP"]
    assert min(r["lower_bound"] for r in boot) == NOW - timedelta(days=3) and max(r["upper_bound"] for r in boot) == NOW
    assert h.one("SELECT 1 AS x FROM raw_events WHERE elastic_id = %s", (old["_id"],))
    assert h.one("SELECT count(*) AS n FROM raw_events WHERE source_id = %s", (sysmon,))["n"] == 16
    assert h.one("SELECT count(*) AS n FROM app_settings WHERE key LIKE 'bootstrap.%%'")["n"] == 0

    again = bootstrap(h, NOW - timedelta(days=1), NOW)
    assert again.status_code == 202  # the previous plan is finished; a new one is accepted


def test_first_sync_and_wizard_bootstrap_at_the_same_moment_do_not_deadlock(h, monkeypatch):
    """Saving the connection wakes the scheduler while the wizard posts its bootstrap. The first sync held the new
    checkpoint row and waited for the plan row the bootstrap held, which waited for the checkpoint: PostgreSQL
    aborted one of them (seen in the lab), and the wizard showed an error after the connection was saved."""
    from ir_engine.sync import scheduler as scheduler_module

    h.configure()
    sysmon = h.source_id("sysmon")
    source = h.one("SELECT * FROM source_configs WHERE id = %s", (sysmon,))
    checkpoint_taken = threading.Event()
    real_ensure = config.ensure_checkpoint

    def slow_ensure(conn, source_id):
        row = real_ensure(conn, source_id)
        if threading.current_thread().name == "first-sync":
            checkpoint_taken.set()
            time.sleep(0.5)  # the bootstrap request arrives right now
        return row

    monkeypatch.setattr(config, "ensure_checkpoint", slow_ensure)
    errors = []

    def first_sync():
        try:
            with psycopg.connect(h.dsn, row_factory=dict_row) as conn:
                scheduler_module.prepare_checkpoint(conn, source, config.get_settings(conn), NOW)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    worker = threading.Thread(target=first_sync, name="first-sync")
    worker.start()
    checkpoint_taken.wait(5)
    response = bootstrap(h, NOW - timedelta(days=3), NOW, source_ids=[sysmon])
    worker.join(10)
    assert not errors and response.status_code == 202, (errors, response.text)
    plan = config.get_plan(psycopg.connect(h.dsn, row_factory=dict_row), sysmon)
    assert plan["from"] == NOW - timedelta(days=3) and plan["auto"] is False


def test_status_reflects_configuration(h):
    response = h.api.get("/sync/status")
    assert_conforms(response, "getSyncStatus")
    assert response.json() == {"scheduler": "blocked", "sources": []}
    h.configure()
    status = h.api.get("/sync/status").json()
    assert status["scheduler"] == "paused" and len(status["sources"]) == 2


def test_background_thread_picks_up_run_now_and_pause(h):
    """run-now must wake a scheduler that is sleeping out its interval, not wait for the next 5-minute tick."""
    h.configure()
    idle = threading.Event()
    tick = h.scheduler.tick

    def observed_tick() -> bool:
        idle.clear()
        more = tick()
        if not more:
            idle.set()
        return more

    h.scheduler.tick = observed_tick
    h.scheduler._wake.clear()  # drop the wake from PUT /config/elk so the thread sleeps right after its first pass
    h.scheduler.start()
    try:
        assert idle.wait(60), "first sync pass did not finish"
        idle.clear()
        h.clock.advance(minutes=10)
        queued = h.api.post("/sync/run-now")
        assert queued.status_code == 202 and len(queued.json()["runs"]) == 2
        assert idle.wait(60), "run-now did not wake the scheduler"
        ids = [r["id"] for r in queued.json()["runs"]]
        assert {r["status"] for r in h.rows("SELECT status FROM sync_runs WHERE id = ANY(%s)", (ids,))} == {"SUCCEEDED"}
        assert h.api.post("/sync/pause").status_code == 202
        assert h.api.get("/sync/status").json()["scheduler"] == "paused"
    finally:
        h.scheduler.stop()
