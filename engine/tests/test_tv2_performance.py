"""Opt-in benchmark: IR_TV2_PERF=1 pytest -s tests/test_tv2_performance.py."""

import importlib.util
import os
import statistics
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from ir_engine.correlation.worker import run_once
from ir_engine.fixtures import load
from ir_engine.investigation.service import search

from .conftest import FIXTURES


@pytest.mark.skipif(os.environ.get("IR_TV2_PERF") != "1", reason="100k benchmark is opt-in")
def test_search_100k(dsn, tmp_path):
    spec = importlib.util.spec_from_file_location("tv2_generator", FIXTURES / "generate.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    hits = generator.perf(generator.Recorder(), 100000)
    generator.write("benchmark", hits, tmp_path)
    load(dsn, [tmp_path], source_name="performance", kind="log")
    start = time.perf_counter()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        while run_once(conn, 1000):
            pass
        conn.execute("ANALYZE")
        durations = []
        for index in range(30):
            begin = time.perf_counter()
            result = search(conn, "domain", f"h{index}.perf.example", "exact", None, None, None, None, None, 100)
            assert result["total"] > 0
            durations.append(time.perf_counter() - begin)
    p95 = sorted(durations)[28]
    print(
        f"100k events; processing_s={time.perf_counter() - start:.3f}; search_p95_s={p95:.6f}; median_s={statistics.median(durations):.6f}"
    )
    assert p95 < 1
