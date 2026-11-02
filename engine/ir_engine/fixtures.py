import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .contracts import SourceKind, TimestampStrategy
from .ingest.writer import SourceRef, write_hits

FIXTURE_CLUSTER = "fixture-cluster"


def read_ndjson(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def ensure_fixture_source(conn: psycopg.Connection, name: str, kind: SourceKind) -> SourceRef:
    conn.execute(
        """
        INSERT INTO elk_connections (url, cluster_key, cluster_name, credential_ref, ca_fingerprint)
        VALUES ('https://fixtures.invalid:9200', %s, 'fixtures', 'fixture', 'fixture')
        ON CONFLICT (cluster_key) DO NOTHING
        """,
        (FIXTURE_CLUSTER,),
    )
    conn_id = conn.execute("SELECT id FROM elk_connections WHERE cluster_key = %s", (FIXTURE_CLUSTER,)).fetchone()["id"]
    strategy, field = (TimestampStrategy.INGESTED, "event.ingested") if kind == SourceKind.LOG else (TimestampStrategy.ALERT, "@timestamp")
    conn.execute(
        """
        INSERT INTO source_configs (connection_id, name, kind, index_patterns, timestamp_strategy, timestamp_field)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (connection_id, name) DO NOTHING
        """,
        (conn_id, name, kind.value, ["fixtures"], strategy.value, field),
    )
    source_id = conn.execute(
        "SELECT id FROM source_configs WHERE connection_id = %s AND name = %s", (conn_id, name)
    ).fetchone()["id"]
    return SourceRef(source_id, FIXTURE_CLUSTER, kind)


def load(dsn: str, paths: list[Path], source_name: str = "sysmon", kind: str = "log") -> dict:
    source_kind = SourceKind(kind)
    totals = {"files": 0, "fetched": 0, "inserted": 0, "updated": 0, "duplicates": 0}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        source = ensure_fixture_source(conn, source_name, source_kind)
        conn.commit()
        for path in paths:
            files = sorted(path.glob("*.ndjson")) if path.is_dir() else [path]
            for file in files:
                stats = write_hits(conn, source, read_ndjson(file))
                totals["files"] += 1
                for key in ("fetched", "inserted", "updated", "duplicates"):
                    totals[key] += getattr(stats, key)
    return totals
