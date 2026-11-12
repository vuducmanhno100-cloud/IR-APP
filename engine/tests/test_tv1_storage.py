import sys
import time
from datetime import datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ir_engine import fixtures
from ir_engine.contracts import SourceKind
from ir_engine.ingest.writer import write_hits
from ir_engine.runtime import upgrade
from ir_engine.runtime.postgres import EmbeddedPostgres, installed_bin_dirs
from ir_engine.storage import retention
from ir_engine.storage import usage as storage_usage
from ir_engine.storage.usage import threshold_state
from ir_engine.sync import scheduler as scheduler_module
from ir_engine.testing.contract import assert_conforms
from ir_engine.testing.fake_es import FakeElasticsearch
from ir_engine.tools.bench_sync import events

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
    assert body["growth_bytes_per_day"] is None

    used = body["used_bytes"]
    h.execute(
        "INSERT INTO app_settings (key, value) VALUES ('usage_samples', %s)",
        (Jsonb([[iso(NOW - timedelta(hours=12)), used - 1_000_000]]),),
    )
    grown = h.api.get("/storage/usage")
    assert_conforms(grown, "getStorageUsage")
    assert grown.json()["growth_bytes_per_day"] == pytest.approx(2_000_000, rel=0.05)
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


def unreadable_data_folder(*args, **kwargs):
    raise PermissionError(13, "Access is denied", r"C:\Users\lab\AppData\Local\IRDesktop\exports")


def test_sync_continues_when_the_storage_check_fails(h, monkeypatch):
    monkeypatch.setattr(scheduler_module, "usage", unreadable_data_folder)
    h.configure()
    assert h.api.post("/sync/bootstrap", json={"from": iso(DAY_START), "to": iso(NOW)}).status_code == 202
    h.tick_until_idle()
    sysmon = h.source_id("sysmon")
    assert h.one("SELECT count(*) AS n FROM raw_events WHERE source_id = %s", (sysmon,))["n"] == 15
    assert "PermissionError" in h.scheduler.status()["last_error"]

    monkeypatch.undo()
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    assert h.scheduler.status()["last_error"] is None


def test_a_blocked_source_stays_blocked_while_the_storage_check_fails(h, monkeypatch):
    h.configure()
    h.execute("UPDATE app_settings SET value = '1048576' WHERE key = 'quota_bytes'")
    h.tick_until_idle()
    monkeypatch.setattr(scheduler_module, "usage", unreadable_data_folder)
    h.clock.advance(minutes=10)
    h.tick_until_idle()
    assert {s["state"] for s in h.api.get("/config/sources").json()} == {"QUOTA_BLOCKED"}
    assert h.runs(h.source_id("sysmon")) == []


def insert_old_events(dsn: str, count: int, age: timedelta) -> None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        source = fixtures.ensure_fixture_source(conn, "bench", SourceKind.LOG)
        batch = []
        for doc in events(count, hosts=4):
            src = doc["_source"]
            src["@timestamp"] = iso(datetime.fromisoformat(src["@timestamp"].replace("Z", "+00:00")) - age)
            batch.append(doc)
            if len(batch) == 1000:
                write_hits(conn, source, batch)
                batch = []
        if batch:
            write_hits(conn, source, batch)
        conn.commit()


def database_size(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]


def test_blocked_sync_resumes_once_retention_frees_space(h, monkeypatch):
    # WAL belongs to the whole test cluster and moves with the other tests; keep this test to the database itself.
    monkeypatch.setattr(storage_usage, "_optional_size", lambda conn, query: 0)
    insert_old_events(h.dsn, 30_000, age=timedelta(days=10))
    fixtures.load(h.dsn, [FIXTURES])  # newer rows after the old ones, so VACUUM cannot simply cut the file short
    h.configure()
    sysmon = h.source_id("sysmon")
    with psycopg.connect(h.dsn, row_factory=dict_row) as conn:
        used = storage_usage.usage(conn, None, 1)["used_bytes"]
    # The API keeps the quota at 1 GB or more; size it to this small database directly.
    h.execute("UPDATE app_settings SET value = %s WHERE key = 'quota_bytes'", (Jsonb(int(used / 0.92)),))
    h.execute("UPDATE app_settings SET value = '30' WHERE key = 'retention_days'")

    h.tick_until_idle()
    assert {s["state"] for s in h.api.get("/config/sources").json()} == {"QUOTA_BLOCKED"}
    assert h.runs(sysmon) == []
    allocated = database_size(h.dsn)

    # Retention is shortened; the next pass under pressure (10 minutes later) deletes the old events.
    h.execute("UPDATE app_settings SET value = '5' WHERE key = 'retention_days'")
    h.clock.advance(minutes=10)
    h.tick_until_idle()

    assert h.one("SELECT count(*) AS n FROM raw_events r JOIN source_configs s ON s.id = r.source_id WHERE s.name = 'bench'")["n"] == 0
    assert {s["state"] for s in h.api.get("/config/sources").json()} == {"ACTIVE"}
    assert h.runs(sysmon)
    body = h.api.get("/storage/usage")
    assert_conforms(body, "getStorageUsage")
    assert body.json()["threshold_state"] in ("OK", "WARN_70") and body.json()["used_bytes"] == sum(body.json()["breakdown"].values())
    # The files did not shrink; usage fell because PostgreSQL will put new rows into the freed space.
    assert database_size(h.dsn) > allocated * 0.8


def test_usage_counts_whole_files_without_the_free_space_map(dsn, monkeypatch):
    monkeypatch.setattr(storage_usage, "FREESPACE_EXTENSION", "no_such_extension")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert storage_usage.measure_reusable(conn) is None
        assert storage_usage.refresh_reusable(conn, NOW) == 0
        body = storage_usage.usage(conn, None, 32212254720)
        allocated = conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"]
    assert body["breakdown"]["database"] == allocated


def test_free_space_is_remeasured_every_ten_minutes(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert storage_usage.reusable_due(conn, NOW)
        storage_usage.refresh_reusable(conn, NOW)
        assert not storage_usage.reusable_due(conn, NOW + timedelta(minutes=9))
        assert storage_usage.reusable_due(conn, NOW + timedelta(minutes=10))


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


def test_scheduler_records_hourly_usage_samples(h):
    h.configure()
    h.tick_until_idle()
    h.clock.advance(minutes=30)
    h.tick_until_idle()
    assert len(h.one("SELECT value FROM app_settings WHERE key = 'usage_samples'")["value"]) == 1
    h.clock.advance(minutes=31)
    h.tick_until_idle()
    assert len(h.one("SELECT value FROM app_settings WHERE key = 'usage_samples'")["value"]) == 2


def test_installed_postgres_is_found_newest_first(tmp_path):
    for version in ("9.6", "16", "18", "pgAdmin 4"):
        (tmp_path / version / "bin").mkdir(parents=True)
    assert [p.parent.name for p in installed_bin_dirs(tmp_path)] == ["18", "16", "9.6"]
