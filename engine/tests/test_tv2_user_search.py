"""Analysts usually type just the account name; it has to find LAB\\name as well."""

import psycopg
from psycopg.rows import dict_row

from ir_engine.correlation.worker import run_once
from ir_engine.fixtures import load
from ir_engine.testing.contract import assert_conforms, engine_client

from .conftest import FIXTURES


def test_user_search_without_domain(dsn):
    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        run_once(conn)
    with engine_client(dsn) as client:
        totals = {}
        for value in ("LAB\\analyst", "lab\\ANALYST", "analyst"):
            response = client.get("/search/events", params={"type": "user", "value": value})
            assert_conforms(response, "searchEvents")
            totals[value] = response.json()["total"]
        prefix = client.get("/search/events", params={"type": "user", "value": "anal", "match": "prefix"}).json()["total"]
    assert totals == {"LAB\\analyst": 6, "lab\\ANALYST": 6, "analyst": 6} and prefix == 6
