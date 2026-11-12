"""TV2 responses carry UTC timestamps with a Z even when PostgreSQL runs in another time zone.

The embedded cluster takes the Windows time zone, so on a machine set to Vietnam every row comes back as +07:00.
"""

import re

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from ir_engine.correlation.worker import run_once
from ir_engine.fixtures import load
from ir_engine.testing.contract import assert_conforms, engine_client

from .conftest import FIXTURES

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def timestamps(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from timestamps(item)
    elif isinstance(value, list):
        for item in value:
            yield from timestamps(item)
    elif isinstance(value, str) and TIMESTAMP.match(value):
        yield value


def test_timestamps_are_utc_whatever_the_database_time_zone(dsn, tmp_path):
    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    load(dsn, [FIXTURES / "alerts"], source_name="security-alerts", kind="alert")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        run_once(conn)
        dns = conn.execute("SELECT id FROM raw_events WHERE event_code = '22'").fetchone()["id"]
    vietnam = make_conninfo(dsn, options="-c TimeZone=Asia/Ho_Chi_Minh")
    with psycopg.connect(vietnam) as conn:
        assert conn.execute("SHOW TimeZone").fetchone()[0] == "Asia/Ho_Chi_Minh"

    with engine_client(vietnam) as client:
        client.app.state.data_dir = tmp_path
        case = client.post("/cases", json={"title": "UTC"})
        trace = client.get(f"/trace/event/{dns}")
        pinned = client.post(f"/cases/{case.json()['id']}/items", json={"item_type": "tree_snapshot", "snapshot": trace.json()})
        responses = {
            "createCase": case,
            "traceEvent": trace,
            "addCaseItem": pinned,
            "searchEvents": client.get("/search/events", params={"type": "domain", "value": "suspicious.example"}),
            "getEvent": client.get(f"/events/{dns}"),
            "listAlerts": client.get("/alerts"),
            "listCases": client.get("/cases"),
            "getCase": client.get(f"/cases/{case.json()['id']}"),
            "exportCase": client.post(f"/cases/{case.json()['id']}/export", json={"format": "html"}),
        }
        found = []
        for operation, response in responses.items():
            assert_conforms(response, operation)
            found += [(operation, stamp) for stamp in timestamps(response.json())]

        assert {operation for operation, _ in found} >= {"traceEvent", "searchEvents", "listAlerts", "getCase", "exportCase"}
        assert [item for item in found if not item[1].endswith("Z")] == []
        assert responses["getCase"].json()["items"][0]["snapshot"] == trace.json()
