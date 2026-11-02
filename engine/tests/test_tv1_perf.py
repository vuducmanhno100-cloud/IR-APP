import copy
import json
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from ir_engine.contracts import SourceKind
from ir_engine.fixtures import ensure_fixture_source, read_ndjson
from ir_engine.ingest.identity import canonical_key, get_path, host_key, normalize_index, parse_ts
from ir_engine.ingest.writer import INSERT_LOG, WriteStats, _base_values, write_hits
from ir_engine.tools import bench_sync

from .conftest import FIXTURES, PASSWORD


def write_hit_by_hit(conn: psycopg.Connection, source, hits: list[dict]) -> WriteStats:
    """The original writer (one host upsert and one insert per hit), kept as the reference for equivalence."""
    stats = WriteStats()
    with conn.transaction(), conn.cursor() as cur:
        for hit in hits:
            stats.fetched += 1
            index = normalize_index(hit["_index"])
            stats.indices[index] = stats.indices.get(index, 0) + 1
            src = hit["_source"]
            hkey = host_key(src)
            seen = parse_ts(get_path(src, "@timestamp"))
            host_id = None
            if hkey and seen:
                host_id = cur.execute(
                    """
                    INSERT INTO hosts (host_key, host_name, agent_id, first_seen, last_seen) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (host_key) DO UPDATE SET
                        host_name = COALESCE(EXCLUDED.host_name, hosts.host_name),
                        agent_id = COALESCE(EXCLUDED.agent_id, hosts.agent_id),
                        first_seen = LEAST(hosts.first_seen, EXCLUDED.first_seen),
                        last_seen = GREATEST(hosts.last_seen, EXCLUDED.last_seen)
                    RETURNING id
                    """,
                    (hkey, get_path(src, "host.name"), get_path(src, "agent.id"), seen, seen),
                ).fetchone()["id"]
            if cur.execute(INSERT_LOG, _base_values(source, hit, host_id, canonical_key(src, hkey))).fetchone():
                stats.inserted += 1
            else:
                stats.duplicates += 1
    return stats


@pytest.fixture
def second_dsn(pg, template_db):
    name = "t_" + uuid.uuid4().hex[:12]
    admin = pg.dsn(PASSWORD, "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"')
    yield pg.dsn(PASSWORD, name)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def tricky_hits() -> list[dict]:
    """Fixtures plus synthetic events, with duplicates inside one batch, renamed hosts and hits without host.name."""
    hits = [h for f in sorted(FIXTURES.glob("*.ndjson")) for h in read_ndjson(f)]
    hits += list(bench_sync.events(300, hosts=3))
    renamed = copy.deepcopy(hits[0])
    renamed["_id"], renamed["_source"]["winlog"]["record_id"] = "renamed", "990001"
    renamed["_source"]["host"]["name"] = "ws-01-renamed"
    nameless = copy.deepcopy(hits[1])
    nameless["_id"], nameless["_source"]["winlog"]["record_id"] = "nameless", "990002"
    del nameless["_source"]["host"]["name"]
    late_rename = copy.deepcopy(renamed)
    late_rename["_id"], late_rename["_source"]["winlog"]["record_id"] = "late-rename", "990003"
    late_nameless = copy.deepcopy(nameless)
    late_nameless["_id"], late_nameless["_source"]["winlog"]["record_id"] = "late-nameless", "990004"
    hits[5:5] = [renamed, copy.deepcopy(renamed), nameless]
    return hits + [late_rename, late_nameless]


def snapshot(dsn: str) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        hosts = conn.execute("SELECT host_key, host_name, agent_id, first_seen, last_seen FROM hosts ORDER BY host_key").fetchall()
        events = conn.execute(
            """
            SELECT r.cluster_key, r.index_name, r.elastic_id, r.canonical_key, h.host_key, r.event_code, r.event_kind,
                   r.event_time, r.ingest_time, r.created_time, r.payload, r.process_state
            FROM raw_events r LEFT JOIN hosts h ON h.id = r.host_id
            ORDER BY r.id
            """
        ).fetchall()
    return {"hosts": hosts, "events": events}


def write_all(dsn: str, writer, batches: list[list[dict]]) -> list[tuple]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        source = ensure_fixture_source(conn, "sysmon", SourceKind.LOG)
        conn.commit()
        return [(s.fetched, s.inserted, s.duplicates, s.indices) for s in (writer(conn, source, b) for b in batches)]


@pytest.mark.parametrize("batch_size", [7, 1000])
def test_batched_writer_stores_exactly_what_the_hit_by_hit_writer_stored(dsn, second_dsn, batch_size):
    hits = tricky_hits()
    batches = [hits[i : i + batch_size] for i in range(0, len(hits), batch_size)]
    assert write_all(dsn, write_hits, batches) == write_all(second_dsn, write_hit_by_hit, batches)
    assert snapshot(dsn) == snapshot(second_dsn)
    assert next(h for h in snapshot(dsn)["hosts"] if h["host_key"].endswith("0a01"))["host_name"] == "ws-01-renamed"

    reread = [hits[:50]]
    assert write_all(dsn, write_hits, reread) == write_all(second_dsn, write_hit_by_hit, reread)
    assert snapshot(dsn) == snapshot(second_dsn)


def test_bench_tool_reports_ingest_footprint_and_queries(dsn):
    result = bench_sync.run(dsn, total=2000, batch_size=500, hosts=4)
    assert result["ingest"]["inserted"] == 2000 and result["ingest"]["batches"] == 4
    assert result["ingest"]["events_per_s"] > 0 and result["footprint"]["bytes_per_event"] > 0
    assert set(result["queries"]) == {"sync_status_ms", "storage_usage_ms", "reconcile_counts_24h_ms"}
    assert result["retention"]["retention_deleted"] > 0
    assert json.loads(json.dumps(result, default=str))


def test_jsonb_payload_is_unchanged_by_batching(dsn):
    hit = read_ndjson(FIXTURES / "01_normal_tree.ndjson")[0]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        source = ensure_fixture_source(conn, "sysmon", SourceKind.LOG)
        conn.commit()
        write_hits(conn, source, [hit])
        stored = conn.execute("SELECT payload FROM raw_events").fetchone()["payload"]
    assert stored == hit["_source"]
