import sys
import time
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from ir_engine import fixtures
from ir_engine.runtime import upgrade
from ir_engine.runtime.postgres import EmbeddedPostgres
from ir_engine.storage import retention
from ir_engine.storage.usage import threshold_state
from ir_engine.testing.contract import assert_conforms
from ir_engine.testing.fake_es import FakeElasticsearch

from .conftest import FIXTURES, PASSWORD
from .tv1_support import DAY_START, NOW, Clock, Harness, iso


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


def test_storage_usage_reports_footprint_and_thresholds(h, tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "case-1.html").write_bytes(b"x" * 4096)
    h.api.app.state.data_dir = tmp_path
    fixtures.load(h.dsn, [FIXTURES])

    response = h.api.get("/storage/usage")
    assert_conforms(response, "getStorageUsage")
    body = response.json()
    assert body["quota_bytes"] == 32212254720 and body["threshold_state"] == "OK"
    assert body["breakdown"]["database"] > 0 and body["breakdown"]["exports"] == 4096
    assert body["used_bytes"] == sum(body["breakdown"].values())
    assert body["growth_bytes_per_day"] > 0
    assert [threshold_state(p) for p in (69.9, 70, 80, 90, 120)] == ["OK", "WARN_70", "WARN_80", "BLOCK_90", "BLOCK_90"]


def test_quota_blocks_ingest_and_releases_when_raised(h):
    h.configure()
    sysmon = h.source_id("sysmon")
    h.execute("UPDATE app_settings SET value = '1048576' WHERE key = 'quota_bytes'")
    h.tick_until_idle()
    assert {s["state"] for s in h.api.get("/config/sources").json()} == {"QUOTA_BLOCKED"}
    status = h.api.get("/sync/status")
    assert_conforms(status, "getSyncStatus")
    assert status.json()["scheduler"] == "blocked"
    assert h.runs(sysmon) == []
    assert h.api.get("/storage/usage").json()["threshold_state"] == "BLOCK_90"

    settings = h.api.get("/config/settings").json()
    assert h.api.put("/config/settings", json={**settings, "quota_bytes": 10 * 1024**3}).status_code == 200
    h.tick_until_idle()
    assert {s["state"] for s in h.api.get("/config/sources").json()} == {"ACTIVE"}
    assert h.runs(sysmon)


def test_retention_keeps_events_pinned_to_a_case(h):
    h.configure()
    assert h.api.post("/sync/bootstrap", json={"from": iso(DAY_START), "to": iso(NOW)}).status_code == 202
    h.tick_until_idle()
    total = h.one("SELECT count(*) AS n FROM raw_events")["n"]
    pinned = h.one("SELECT id FROM raw_events WHERE event_code = '22' LIMIT 1")["id"]
    case_id = h.one("INSERT INTO incident_cases (title) VALUES ('c') RETURNING id")["id"]
    h.execute("INSERT INTO case_items (case_id, item_type, ref_id) VALUES (%s, 'event', %s)", (case_id, pinned))

    with psycopg.connect(h.dsn, row_factory=dict_row) as conn:
        assert retention.apply(conn, NOW + timedelta(days=2), retention_days=1, batch_size=4) == total - 1
    assert [r["id"] for r in h.rows("SELECT id FROM raw_events")] == [pinned]
    assert h.one("SELECT count(*) AS n FROM sync_runs")["n"] == 0
    assert h.one("SELECT count(*) AS n FROM hosts")["n"] == 2


def test_scheduler_runs_retention_hourly(h):
    h.configure()
    assert h.api.post("/sync/bootstrap", json={"from": iso(DAY_START), "to": iso(NOW)}).status_code == 202
    h.tick_until_idle()
    settings = h.api.get("/config/settings").json()
    h.api.put("/config/settings", json={**settings, "retention_days": 1})
    h.clock.advance(days=2)
    h.tick_until_idle()
    assert h.one("SELECT count(*) AS n FROM raw_events WHERE event_time < %s", (NOW,))["n"] == 0


def test_backup_and_restore_round_trip(pg, dsn, tmp_path):
    name = psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('marker', '\"before\"')")
    dump = pg.dump(PASSWORD, tmp_path / "backups" / "irdesktop-v0002-test.dump", dbname=name)
    assert dump.exists() and dump.stat().st_size > 0
    assert not list(pg.data_dir.parent.glob("irpass*"))
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("UPDATE app_settings SET value = '\"after\"' WHERE key = 'marker'")
    pg.restore(PASSWORD, dump, dbname=name)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT value FROM app_settings WHERE key = 'marker'").fetchone()[0] == "before"
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 2


def test_backup_only_when_migrations_are_pending(pg, dsn, tmp_path):
    assert upgrade.backup_before_migrate(pg, PASSWORD, dsn, tmp_path / "backups") is None
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = 2")
    backup = upgrade.backup_before_migrate(pg, PASSWORD, dsn, tmp_path / "backups")
    assert backup is not None and backup.name.startswith("irdesktop-v0001-") and backup.stat().st_size > 0
    for i in range(5):
        (tmp_path / "backups" / f"irdesktop-v0001-old{i}.dump").write_bytes(b"")
    upgrade.prune(tmp_path / "backups", keep=3)
    assert len(list((tmp_path / "backups").glob("*.dump"))) == 3


def test_failed_migration_points_to_the_backup(pg, dsn, tmp_path):
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = 2")
    with pytest.raises(upgrade.UpgradeError, match="backup restore --file") as failure:
        upgrade.migrate_with_backup(pg, PASSWORD, dsn, tmp_path / "backups")
    assert str(next((tmp_path / "backups").glob("*.dump"))) in str(failure.value)


def test_tool_output_does_not_wait_for_processes_the_tool_leaves_running(tmp_path):
    """pg_ctl start leaves the postmaster running with pg_ctl's output handles (Windows); _run must still return."""
    script = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'], stdout=sys.stdout, stderr=sys.stderr); "
        "print('started')"
    )
    started = time.monotonic()
    output = EmbeddedPostgres(tmp_path, tmp_path)._run(sys.executable, "-c", script, timeout=20)
    assert output == "started" and time.monotonic() - started < 4
