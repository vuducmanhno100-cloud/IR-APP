import hashlib
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from ir_engine.correlation.worker import run_once
from ir_engine.fixtures import load
from ir_engine.testing.contract import assert_conforms, engine_client

from .conftest import FIXTURES


def prepare(dsn):
    load(dsn, [FIXTURES], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert run_once(conn) == 15
        assert run_once(conn) == 0


def test_fixture_correlation(dsn):
    prepare(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) n FROM event_process_links").fetchone()["n"] == 15
        assert conn.execute("SELECT count(*) n FROM raw_events WHERE process_state='PENDING'").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) n FROM process_instances WHERE pid=4242").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) n FROM process_edges WHERE level='VERIFIED'").fetchone()["n"] >= 4


def test_all_tv2_endpoints(dsn, monkeypatch, tmp_path):
    prepare(dsn)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    load(dsn, [FIXTURES / "alerts"], source_name="security-alerts", kind="alert")
    with engine_client(dsn) as client:

        def check(method, path, op, status=200, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.status_code == status, response.text
            assert_conforms(response, op)
            return response.json() if response.content else None

        result = check("GET", "/search/events?type=domain&value=suspicious.example", "searchEvents")
        assert result["total"] == 1
        ident = result["items"][0]["id"]
        detail = check("GET", f"/events/{ident}", "getEvent")
        assert detail["payload"]
        tree = check("GET", f"/trace/event/{ident}", "traceEvent")
        assert tree["matched_event_ids"] == [ident]
        assert tree["completeness"] == "MISSING_PARENT"
        assert len(tree["nodes"]) == 5
        check("GET", f"/trace/expand/{tree['focus_process_id']}", "expandProcess")
        check("GET", "/trace/unresolved", "listUnresolved")
        alerts = check("GET", "/alerts", "listAlerts")
        assert alerts["items"][0]["original_event_id"] == ident
        for mode, value, kind, extra in [
            ("prefix", "suspicious", "domain", ""),
            ("suffix", "example", "domain", ""),
            ("cidr", "203.0.113.0/24", "ip", ""),
            ("contains", "powershell", "command_line", "&from=2026-01-01T00:00:00Z&to=2027-01-01T00:00:00Z"),
        ]:
            check("GET", f"/search/events?type={kind}&value={value}&match={mode}{extra}", "searchEvents")
        check("GET", "/cases", "listCases")
        case = check("POST", "/cases", "createCase", 201, json={"title": "<script>case</script>"})
        cid = case["id"]
        check("PATCH", f"/cases/{cid}", "updateCase", json={"notes": "reviewed", "status": "IN_PROGRESS"})
        item = check("POST", f"/cases/{cid}/items", "addCaseItem", 201, json={"item_type": "event", "ref_id": ident})
        check("GET", f"/cases/{cid}", "getCase")
        exported = check("POST", f"/cases/{cid}/export", "exportCase", json={"format": "html"})
        content = Path(exported["artifact_path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == exported["sha256"]
        assert b"<script>case</script>" not in content
        check("DELETE", f"/cases/{cid}/items/{item['id']}", "removeCaseItem", 204)
        for path, op in [
            ("/events/999999", "getEvent"),
            ("/trace/event/999999", "traceEvent"),
            ("/trace/expand/999999", "expandProcess"),
            ("/cases/999999", "getCase"),
        ]:
            check("GET", path, op, 404)


def test_late_parent_and_replay(dsn):
    load(dsn, [FIXTURES], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        while run_once(conn, 1):
            pass
        late = conn.execute("SELECT * FROM process_instances WHERE name='mshta.exe'").fetchone()
        assert late and not late["is_placeholder"]
        assert late["attr_source"] == "self"
        assert (
            conn.execute(
                "SELECT count(*) n FROM unresolved_relations WHERE target_guid=%s AND status='OPEN'", (late["process_guid"],)
            ).fetchone()["n"]
            == 0
        )
        before = conn.execute("SELECT count(*) n FROM process_edges").fetchone()["n"]
        conn.execute("UPDATE raw_events SET process_state='PENDING'")
        run_once(conn)
        assert conn.execute("SELECT count(*) n FROM process_edges").fetchone()["n"] == before


def test_search_validation_and_literal_wildcards(dsn):
    prepare(dsn)
    with engine_client(dsn) as client:
        for params in [
            dict(type="ip", value="bad", match="cidr"),
            dict(type="command_line", value="x", match="contains"),
            dict(type="domain", value="x", cursor="broken"),
            dict(type="domain", value="x", **{"from": "bad"}),
        ]:
            response = client.get("/search/events", params=params)
            assert response.status_code == 422, response.text
            assert_conforms(response, "searchEvents")
        response = client.get("/search/events", params={"type": "domain", "value": "%", "match": "prefix"})
        assert_conforms(response, "searchEvents")
        assert response.json()["total"] == 0


def test_tv2_authentication_contract(dsn):
    from ir_engine.testing.contract import operations

    with engine_client(dsn) as client:
        for operation, spec in operations().items():
            if spec.get("x-owner") != "TV2":
                continue
            path = spec["path"]
            for key in ("event_id", "process_id", "case_id", "item_id"):
                path = path.replace("{" + key + "}", "1")
            response = client.request(spec["method"], path, headers={"Authorization": "Bearer invalid"})
            assert response.status_code == 401, response.text
            assert_conforms(response, operation)


def test_fixture_01_exact_expectations(dsn):
    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert run_once(conn) == 7
        assert conn.execute("SELECT count(*) n FROM process_instances").fetchone()["n"] == 5
        assert conn.execute("SELECT count(*) n FROM process_instances WHERE is_placeholder").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) n FROM process_edges").fetchone()["n"] == 4
        assert conn.execute("SELECT count(*) n FROM event_process_links").fetchone()["n"] == 7
        assert conn.execute("SELECT count(*) n FROM unresolved_relations WHERE status='OPEN'").fetchone()["n"] == 1


def test_unlinked_and_cycle_conflict(dsn):
    prepare(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT * FROM raw_events WHERE event_code='1' ORDER BY id LIMIT 1").fetchone()
        from psycopg.types.json import Jsonb

        payload = row["payload"]
        payload["process"]["parent"]["entity_id"] = payload["process"]["entity_id"]
        conn.execute("UPDATE raw_events SET payload=%s,process_state='PENDING' WHERE id=%s", (Jsonb(payload), row["id"]))
        run_once(conn)
        assert conn.execute("SELECT 1 FROM unresolved_relations WHERE event_id=%s AND status='CONFLICT'", (row["id"],)).fetchone()
        payload["process"]["entity_id"] = "invalid"
        conn.execute("UPDATE raw_events SET payload=%s,process_state='PENDING' WHERE id=%s", (Jsonb(payload), row["id"]))
        # A rebuild must clear old derived rows; test fresh unlinked event separately.
        conn.execute("DELETE FROM event_process_links WHERE event_id=%s", (row["id"],))
        run_once(conn)
        assert conn.execute("SELECT process_state FROM raw_events WHERE id=%s", (row["id"],)).fetchone()["process_state"] == "UNLINKED"


def test_orphan_and_duplicates(dsn):
    load(dsn, [FIXTURES / "04_orphan.ndjson", FIXTURES / "05_duplicates.ndjson"], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert run_once(conn) == 2
        orphan = conn.execute(
            "SELECT p.* FROM process_instances p JOIN event_process_links l ON l.process_id=p.id WHERE l.link_type='network'"
        ).fetchone()
        assert orphan["is_placeholder"]
        assert conn.execute(
            "SELECT 1 FROM unresolved_relations WHERE target_guid=%s AND reason='process_create_missing' AND status='OPEN'",
            (orphan["process_guid"],),
        ).fetchone()
        assert conn.execute("SELECT count(*) n FROM raw_events").fetchone()["n"] == 2


def test_search_cursor_and_suffix_boundary(dsn):
    prepare(dsn)
    with engine_client(dsn) as client:
        params = {"type": "event_code", "value": "1", "limit": 1}
        seen = []
        while True:
            response = client.get("/search/events", params=params)
            assert_conforms(response, "searchEvents")
            body = response.json()
            seen.extend(item["id"] for item in body["items"])
            if not body["next_cursor"]:
                break
            params["cursor"] = body["next_cursor"]
        assert len(seen) == len(set(seen))
        assert len(seen) == body["total"]
        response = client.get("/search/events", params={"type": "domain", "value": "ample", "match": "suffix"})
        assert_conforms(response, "searchEvents")
        assert response.json()["total"] == 0


def test_tree_limit_includes_ancestors(dsn):
    from ir_engine.investigation.tree import tree_shape

    from .test_tree_and_migrations import seed

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = seed(conn)
        shape = tree_shape(conn, ids["powershell"], max_depth=10, max_nodes=3)
        assert len(shape.depth_by_id) <= 3
        assert ids["powershell"] in shape.depth_by_id
        assert shape.truncated


def test_late_parent_time_conflict(dsn):
    from psycopg.types.json import Jsonb

    prepare(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        edge = conn.execute(
            "SELECT e.*,p.create_event_id FROM process_edges e JOIN process_instances p ON p.id=e.parent_id WHERE p.create_event_id IS NOT NULL LIMIT 1"
        ).fetchone()
        parent = conn.execute("SELECT * FROM raw_events WHERE id=%s", (edge["create_event_id"],)).fetchone()
        from datetime import timedelta

        later = parent["event_time"] + timedelta(days=1)
        conn.execute("UPDATE process_instances SET started_at=%s WHERE id=%s", (later, edge["parent_id"]))
        payload = parent["payload"]
        conn.execute("UPDATE raw_events SET payload=%s,process_state='PENDING' WHERE id=%s", (Jsonb(payload), parent["id"]))
        run_once(conn)
        assert not conn.execute("SELECT 1 FROM process_edges WHERE id=%s", (edge["id"],)).fetchone()
        assert conn.execute("SELECT 1 FROM unresolved_relations WHERE reason='parent_created_after_child' AND status='CONFLICT'").fetchone()


def test_malformed_event_does_not_block_batch(dsn):
    from psycopg.types.json import Jsonb

    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT id,payload FROM raw_events ORDER BY id LIMIT 1").fetchone()
        payload = row["payload"]
        payload["process"]["executable"] = {"malformed": "value"}
        conn.execute("UPDATE raw_events SET payload=%s WHERE id=%s", (Jsonb(payload), row["id"]))
        assert run_once(conn) == 7
        state = conn.execute("SELECT process_state,error FROM raw_events WHERE id=%s", (row["id"],)).fetchone()
        assert state == {"process_state": "FAILED", "error": "ValueError"}
        assert conn.execute("SELECT count(*) n FROM raw_events WHERE process_state='CORRELATED'").fetchone()["n"] == 6


def test_failed_event_raw_is_readable(dsn):
    from psycopg.types.json import Jsonb

    load(dsn, [FIXTURES / "01_normal_tree.ndjson"], source_name="sysmon", kind="log")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT id,payload FROM raw_events ORDER BY id LIMIT 1").fetchone()
        payload = row["payload"]
        payload["process"]["executable"] = {"broken": True}
        conn.execute("UPDATE raw_events SET payload=%s WHERE id=%s", (Jsonb(payload), row["id"]))
        run_once(conn)
    with engine_client(dsn) as client:
        response = client.get(f"/events/{row['id']}")
        assert response.status_code == 200
        assert_conforms(response, "getEvent")
        assert response.json()["payload"] == payload
        assert response.json()["process_state"] == "FAILED"
