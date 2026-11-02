import os
from pathlib import Path

import psycopg

THRESHOLDS = ((90, "BLOCK_90"), (80, "WARN_80"), (70, "WARN_70"))


def dir_size(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _optional_size(conn: psycopg.Connection, query: str) -> int:
    """WAL and temp listings need superuser or pg_monitor; the embedded cluster has it, a dev database may not."""
    try:
        with conn.transaction():
            row = conn.execute(query).fetchone()
        return int(row["n"] or 0)
    except psycopg.Error:
        return 0


def threshold_state(percent: float) -> str:
    return next((state for limit, state in THRESHOLDS if percent >= limit), "OK")


def growth_per_day(conn: psycopg.Connection, database_bytes: int) -> int | None:
    """Bytes per day, estimated as the database footprint per event times events received in the last 24 hours."""
    row = conn.execute(
        """
        SELECT count(*) AS total, count(*) FILTER (WHERE local_received_at >= now() - interval '1 day') AS recent
        FROM raw_events
        """
    ).fetchone()
    if not row["total"]:
        return None
    return int(database_bytes * row["recent"] / row["total"])


def usage(conn: psycopg.Connection, data_dir: Path | None, quota_bytes: int, with_growth: bool = False) -> dict:
    database = int(conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"])
    wal = _optional_size(conn, "SELECT coalesce(sum(size), 0) AS n FROM pg_ls_waldir()")
    temp = _optional_size(conn, "SELECT coalesce(sum(size), 0) AS n FROM pg_ls_tmpdir()")
    exports = dir_size(data_dir / "exports") if data_dir else 0
    used = database + wal + temp + exports
    percent = round(used * 100 / quota_bytes, 2) if quota_bytes else 100.0
    return {
        "quota_bytes": quota_bytes,
        "used_bytes": used,
        "percent": percent,
        "threshold_state": threshold_state(percent),
        "growth_bytes_per_day": growth_per_day(conn, database) if with_growth else None,
        "breakdown": {"database": database, "wal": wal, "exports": exports, "temp": temp},
    }
