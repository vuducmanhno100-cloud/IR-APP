"""Additional acceptance cases referenced by fixtures/sysmon/TV2_TEST_CASES.md."""

import hashlib
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ir_engine.correlation.worker import run_once
from ir_engine.fixtures import load
from ir_engine.testing.contract import assert_conforms, engine_client

from .conftest import FIXTURES


@pytest.mark.parametrize("different_boot", [False, True])
def test_boot_compatibility(dsn, different_boot):
    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT id,payload FROM raw_events WHERE event_code='1' ORDER BY event_time,id").fetchall()
        for index, row in enumerate(rows):
            payload = row["payload"]
            payload["host"]["boot"] = {"id": "other" if different_boot and index == 1 else "same"}
            conn.execute("UPDATE raw_events SET payload=%s WHERE id=%s", (Jsonb(payload), row["id"]))
        run_once(conn)
        conflicts = conn.execute(
            "SELECT count(*) n FROM unresolved_relations WHERE reason='boot_conflict' AND status='CONFLICT'"
        ).fetchone()["n"]
        assert (conflicts > 0) == different_boot
        if different_boot:
            invalid_edges = conn.execute("""SELECT 1 FROM process_edges e
                JOIN process_instances p ON p.id=e.parent_id JOIN process_instances c ON c.id=e.child_id
                JOIN raw_events pr ON pr.id=p.create_event_id JOIN raw_events cr ON cr.id=c.create_event_id
                WHERE pr.payload #>> '{host,boot,id}' <> cr.payload #>> '{host,boot,id}' """).fetchall()
            assert invalid_edges == []


def test_case_audit_snapshot_hash_and_cross_case_delete(dsn, tmp_path):
    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        run_once(conn)
        ident = conn.execute("SELECT id FROM raw_events WHERE event_code='22'").fetchone()["id"]
    with engine_client(dsn) as client:
        client.app.state.data_dir = tmp_path

        def call(method, path, op, status=200, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.status_code == status, response.text
            assert_conforms(response, op)
            return response.json() if response.content else None

        first = call("POST", "/cases", "createCase", 201, json={"title": "Evidence <script>x</script>"})["id"]
        second = call("POST", "/cases", "createCase", 201, json={"title": "Other"})["id"]
        snapshot = call("GET", f"/trace/event/{ident}", "traceEvent")
        item = call("POST", f"/cases/{first}/items", "addCaseItem", 201, json={"item_type": "tree_snapshot", "snapshot": snapshot})
        call("DELETE", f"/cases/{second}/items/{item['id']}", "removeCaseItem", 404)
        detail = call("GET", f"/cases/{first}", "getCase")
        assert detail["items"][0]["snapshot"] == snapshot
        exported = call("POST", f"/cases/{first}/export", "exportCase", json={"format": "html"})
        content = Path(exported["artifact_path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == exported["sha256"]
        assert b"<script>" not in content
        call("DELETE", f"/cases/{first}/items/{item['id']}", "removeCaseItem", 204)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]
        assert actions == ["case.create", "case.create", "case.item.add", "case.export", "case.item.remove"]


@pytest.mark.parametrize(
    "kind,value,mode",
    [
        ("domain", "suspicious.example", "exact"),
        ("domain", "suspicious", "prefix"),
        ("domain", "example", "suffix"),
        ("command_line", "powershell", "contains"),
        ("ip", "203.0.113.0/24", "cidr"),
    ],
)
def test_search_modes_against_term_ground_truth(dsn, kind, value, mode):
    from ipaddress import ip_address, ip_network

    load(dsn, [FIXTURES], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        run_once(conn)
        terms = conn.execute("SELECT event_id,term_value FROM event_search_terms WHERE term_type=%s", (kind,)).fetchall()

    def matches(term):
        if mode == "exact":
            return term == value
        if mode == "prefix":
            return term.startswith(value)
        if mode == "suffix":
            return term == value or term.endswith("." + value)
        if mode == "contains":
            return value in term
        return ip_address(term) in ip_network(value)

    expected = {row["event_id"] for row in terms if matches(row["term_value"])}
    assert expected
    with engine_client(dsn) as client:
        response = client.get(
            "/search/events",
            params={
                "type": kind,
                "value": value,
                "match": mode,
                "limit": 500,
                "from": "2026-01-01T00:00:00Z",
                "to": "2027-01-01T00:00:00Z",
            },
        )
        assert response.status_code == 200
        assert_conforms(response, "searchEvents")
        assert {item["id"] for item in response.json()["items"]} == expected
        assert response.json()["total"] == len(expected)
