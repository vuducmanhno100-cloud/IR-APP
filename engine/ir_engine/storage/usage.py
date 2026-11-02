import os
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

from ..sync import config
from ..sync.common import iso, parse_iso, utcnow

THRESHOLDS = ((90, "BLOCK_90"), (80, "WARN_80"), (70, "WARN_70"))
SAMPLES_KEY = "usage_samples"
SAMPLE_EVERY = timedelta(hours=1)
MIN_SPAN = timedelta(hours=1)
KEEP_SAMPLES = 48


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


def samples(conn: psycopg.Connection) -> list[tuple[datetime, int]]:
    return [(parse_iso(at), int(n)) for at, n in config.get_value(conn, SAMPLES_KEY, [])]


def record_sample(conn: psycopg.Connection, now: datetime, used_bytes: int) -> None:
    """Hourly footprint samples (the scheduler calls this every pass); growth is read from them in O(1)."""
    known = samples(conn)
    if known and now - known[-1][0] < SAMPLE_EVERY:
        return
    known.append((now, used_bytes))
    config.set_value(conn, SAMPLES_KEY, [[iso(at), n] for at, n in known[-KEEP_SAMPLES:]])


def growth_per_day(conn: psycopg.Connection, now: datetime, used_bytes: int) -> int | None:
    """Bytes per day from the sample closest to 24 hours ago (at least 1 hour old). Counting raw_events instead
    would scan the whole table on every call (about 0.3 s per million events)."""
    usable = [(at, n) for at, n in samples(conn) if now - at >= MIN_SPAN]
    if not usable:
        return None
    at, n = min(usable, key=lambda s: abs((now - s[0]) - timedelta(days=1)))
    return max(int((used_bytes - n) / ((now - at) / timedelta(days=1))), 0)


def usage(
    conn: psycopg.Connection, data_dir: Path | None, quota_bytes: int, with_growth: bool = False, now: datetime | None = None
) -> dict:
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
        "growth_bytes_per_day": growth_per_day(conn, now or utcnow(), used) if with_growth else None,
        "breakdown": {"database": database, "wal": wal, "exports": exports, "temp": temp},
    }
