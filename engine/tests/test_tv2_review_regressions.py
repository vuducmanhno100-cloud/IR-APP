from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from ir_engine.investigation.service import cursor_encode, trace
from ir_engine.testing.contract import assert_conforms, engine_client


def test_numeric_cursor_timestamp_returns_422(dsn):
    with engine_client(dsn) as client:
        response = client.get("/search/events", params={"type": "domain", "value": "example.org", "cursor": cursor_encode([123, 1])})
        assert response.status_code == 422
        assert_conforms(response, "searchEvents")


def test_ancestor_cap_is_truncated(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        host = conn.execute("INSERT INTO hosts(host_key,first_seen,last_seen) VALUES ('depth-test',now(),now()) RETURNING id").fetchone()[
            "id"
        ]
        previous = None
        for number in range(53):
            current = conn.execute(
                "INSERT INTO process_instances(host_id,process_guid) VALUES (%s,%s) RETURNING id", (host, str(number))
            ).fetchone()["id"]
            if previous is not None:
                conn.execute(
                    "INSERT INTO process_edges(parent_id,child_id,level,rule_ver) VALUES (%s,%s,'VERIFIED','test')", (previous, current)
                )
            previous = current
        assert trace(conn, process_id=current)["completeness"] == "TRUNCATED"
        assert trace(conn, process_id=current, expand=True)["completeness"] == "COMPLETE"


def test_export_uses_configured_data_dir(dsn, tmp_path):
    with engine_client(dsn) as client:
        client.app.state.data_dir = tmp_path / "configured"
        created = client.post("/cases", json={"title": "Export location"})
        assert_conforms(created, "createCase")
        response = client.post(f"/cases/{created.json()['id']}/export", json={"format": "html"})
        assert response.status_code == 200
        assert_conforms(response, "exportCase")
        output = Path(response.json()["artifact_path"])
        assert output.parent == tmp_path / "configured" / "exports"
        assert output.is_file()
