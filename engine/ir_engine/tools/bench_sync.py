"""Đo hiệu năng phần dữ liệu của TV1 (báo cáo mục 11.2) trên máy đang chạy.

Đo phía engine: ghi raw theo batch (write_hits) và các truy vấn mà engine chạy định kỳ hoặc khi UI gọi.
Thời gian kéo dữ liệu từ Elasticsearch phụ thuộc cluster nên đo trên ELK lab (xem sync_runs).

    python -m ir_engine.tools.bench_sync --events 100000            # PostgreSQL tạm, tự dọn
    python -m ir_engine.tools.bench_sync --events 100000 --dsn ...  # database có sẵn (sẽ ghi dữ liệu thử vào đó)
"""

import argparse
import importlib.util
import json
import os
import secrets
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from ..contracts import SourceKind
from ..db.migrate import migrate
from ..fixtures import ensure_fixture_source
from ..ingest.writer import write_hits
from ..runtime.postgres import EmbeddedPostgres, find_bin_dir
from ..storage import retention
from ..storage.usage import usage
from ..sync import control, runner

GENERATOR = Path(__file__).resolve().parents[3] / "fixtures" / "sysmon" / "generate.py"
DEFAULT_QUOTA = 32212254720


def load_generator():
    if not GENERATOR.exists():
        raise SystemExit(f"Không thấy {GENERATOR}; công cụ này chạy từ repo, không chạy từ bản đóng gói")
    spec = importlib.util.spec_from_file_location("ir_fixture_generate", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def events(total: int, hosts: int = 8) -> Iterator[dict]:
    """The same 5-event process pattern as `generate.py --perf`, streamed so 1M events never sit in memory."""
    gen = load_generator()
    gen.HOSTS = {
        f"ws-{i:02d}": {"id": f"8f3c2a71-5b1e-4c0e-9a77-2d6f1b9e{i:04x}", "agent": f"a1f0c9d2-{i:04x}", "ip": f"10.10.20.{i + 10}"}
        for i in range(hosts)
    }
    rec = gen.Recorder()
    names = list(gen.HOSTS)
    produced, n = 0, 0
    while produced < total:
        host, hi = names[n % len(names)], n % len(names)
        t = 7200 + n * 0.05
        parent, child = gen.perf_guid(hi, 2 * n), gen.perf_guid(hi, 2 * n + 1)
        ppid, cpid = 3000 + (2 * n) % 60000, 3001 + (2 * n) % 60000
        pcmd = f"powershell.exe -nop -File job{n}.ps1"
        ip = f"203.0.113.{n % 250 + 1}"
        for doc in (
            rec.create(host, t, parent, ppid, gen.PS, pcmd, gen.guid(1), 4120, gen.EXPLORER, "C:\\Windows\\Explorer.EXE"),
            rec.create(host, t + 0.01, child, cpid, gen.CMD, f"cmd.exe /c task{n}", parent, ppid, gen.PS, pcmd),
            rec.dns(host, t + 0.02, child, cpid, gen.CMD, f"h{n % 5000}.perf.example", [ip]),
            rec.net(host, t + 0.03, child, cpid, gen.CMD, 49152 + n % 16000, ip, 443),
            rec.terminate(host, t + 0.04, child, cpid, gen.CMD),
        ):
            if produced == total:
                return
            produced += 1
            yield doc
        n += 1


def batches(items: Iterator[dict], size: int) -> Iterator[list[dict]]:
    batch: list[dict] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def peak_rss_mb() -> float | None:
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except ImportError:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return round(counters.PeakWorkingSetSize / (1024 * 1024), 1)
    except (AttributeError, OSError):
        return None


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def timed(fn, repeat: int = 3) -> float:
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(samples), 1)


def ingest(conn: psycopg.Connection, total: int, batch_size: int, hosts: int) -> dict:
    """Times write_hits only; generating the synthetic events is excluded."""
    source = ensure_fixture_source(conn, "bench", SourceKind.LOG)
    conn.commit()
    durations, cpu, inserted, duplicates = [], 0.0, 0, 0
    for batch in batches(events(total, hosts), batch_size):
        cpu_start, start = time.process_time(), time.perf_counter()
        stats = write_hits(conn, source, batch)
        durations.append((time.perf_counter() - start) * 1000)
        cpu += time.process_time() - cpu_start
        inserted += stats.inserted
        duplicates += stats.duplicates
    seconds = sum(durations) / 1000
    return {
        "source_id": source.id,
        "events": total,
        "inserted": inserted,
        "duplicates": duplicates,
        "batch_size": batch_size,
        "batches": len(durations),
        "batch_ms_p50": round(percentile(durations, 0.5), 1),
        "batch_ms_p95": round(percentile(durations, 0.95), 1),
        "batch_ms_max": round(max(durations), 1),
        "write_s": round(seconds, 1),
        "events_per_s": round(total / seconds),
        "engine_cpu_share": round(cpu / seconds, 2),
    }


def footprint(conn: psycopg.Connection, events_count: int) -> dict:
    conn.execute("VACUUM (ANALYZE) raw_events")
    row = conn.execute(
        """
        SELECT pg_database_size(current_database()) AS database,
               pg_table_size('raw_events') AS raw_table,
               pg_indexes_size('raw_events') AS raw_indexes
        """
    ).fetchone()
    mb = {k: round(v / 1024**2, 1) for k, v in row.items()}
    return {**{f"{k}_mb": v for k, v in mb.items()}, "bytes_per_event": round(row["database"] / max(events_count, 1))}


def queries(conn: psycopg.Connection, source_id: int, now: datetime) -> dict:
    source = conn.execute("SELECT * FROM source_configs WHERE id = %s", (source_id,)).fetchone()
    lower = conn.execute("SELECT min(ingest_time) AS lo FROM raw_events WHERE source_id = %s", (source_id,)).fetchone()["lo"]
    return {
        "sync_status_ms": timed(lambda: control.source_status(conn, source, now)),
        "storage_usage_ms": timed(lambda: usage(conn, None, DEFAULT_QUOTA, with_growth=True)),
        "reconcile_counts_24h_ms": timed(lambda: runner._local_counts(conn, source_id, "ingest_time", lower, lower + timedelta(hours=24))),
    }


def retention_cost(conn: psycopg.Connection, total: int) -> dict:
    """Delete the oldest ~10% the way the scheduler does, and report the rate."""
    cutoff_row = conn.execute("SELECT event_time FROM raw_events ORDER BY event_time OFFSET %s LIMIT 1", (max(total // 10, 1),)).fetchone()
    if not cutoff_row:
        return {}
    now = cutoff_row["event_time"] + timedelta(days=1)
    start = time.perf_counter()
    deleted = retention.apply(conn, now, retention_days=1)
    seconds = time.perf_counter() - start
    return {
        "retention_deleted": deleted,
        "retention_s": round(seconds, 1),
        "retention_events_per_s": round(deleted / seconds) if seconds else None,
    }


def run(dsn: str, total: int, batch_size: int, hosts: int) -> dict:
    migrate(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        result = {"ingest": ingest(conn, total, batch_size, hosts)}
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        result["footprint"] = footprint(conn, total)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        result["queries"] = queries(conn, result["ingest"]["source_id"], datetime.now(timezone.utc))
        result["retention"] = retention_cost(conn, total)
    result["engine_peak_rss_mb"] = peak_rss_mb()
    return result


@contextmanager
def temporary_postgres() -> Iterator[str]:
    root = Path(tempfile.mkdtemp(prefix="ir-bench-"))
    password = secrets.token_urlsafe(24)
    pg = EmbeddedPostgres(find_bin_dir(), root)
    try:
        pg.init(password)
        pg.start()
        yield pg.ensure_database(password)
    finally:
        pg.stop()
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ir_engine.tools.bench_sync", description=__doc__.splitlines()[0])
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--batch", type=int, default=1000)
    parser.add_argument("--hosts", type=int, default=8)
    parser.add_argument("--dsn", default=os.environ.get("IR_BENCH_DSN"), help="Database có sẵn; mặc định tạo PostgreSQL tạm")
    args = parser.parse_args(argv)
    if args.dsn:
        result = run(args.dsn, args.events, args.batch, args.hosts)
    else:
        with temporary_postgres() as dsn:
            result = run(dsn, args.events, args.batch, args.hosts)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
