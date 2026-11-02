import psycopg
import pytest
from psycopg.rows import dict_row

from ir_engine.db.migrate import MigrationError, current_version, migrate
from ir_engine.investigation.tree import tree_shape


def seed(conn):
    host = conn.execute(
        "INSERT INTO hosts (host_key, host_name, first_seen, last_seen) VALUES ('id:h', 'ws', now(), now()) RETURNING id"
    ).fetchone()["id"]
    names = ["services", "svchost", "explorer", "winword", "powershell", "cmd", "conhost", "whoami", "notepad", "calc"]
    ids = {}
    for name in names:
        ids[name] = conn.execute(
            "INSERT INTO process_instances (host_id, process_guid, name) VALUES (%s, %s, %s) RETURNING id",
            (host, f"g-{name}", f"{name}.exe"),
        ).fetchone()["id"]
    edges = [
        ("services", "svchost", "VERIFIED"),
        ("explorer", "winword", "VERIFIED"),
        ("explorer", "notepad", "VERIFIED"),
        ("explorer", "calc", "VERIFIED"),
        ("winword", "powershell", "VERIFIED"),
        ("powershell", "cmd", "VERIFIED"),
        ("cmd", "conhost", "VERIFIED"),
        ("conhost", "whoami", "VERIFIED"),
        ("svchost", "explorer", "WEAK"),
        ("whoami", "explorer", "WEAK"),
    ]
    for parent, child, level in edges:
        conn.execute(
            "INSERT INTO process_edges (parent_id, child_id, level, rule_ver) VALUES (%s, %s, %s, 'test')",
            (ids[parent], ids[child], level),
        )
    return ids


def test_tree_shape_confirmed_only(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = seed(conn)
        shape = tree_shape(conn, ids["powershell"], max_depth=2)
    by_name = {name: shape.depth_by_id.get(pid) for name, pid in ids.items()}
    assert by_name["explorer"] == -2 and by_name["winword"] == -1 and by_name["powershell"] == 0
    assert by_name["cmd"] == 1 and by_name["conhost"] == 2 and by_name["whoami"] is None
    assert by_name["svchost"] is None and by_name["services"] is None
    assert shape.collapsed[ids["explorer"]] == 2
    assert shape.collapsed[ids["conhost"]] == 1
    assert shape.truncated


def test_tree_shape_inferred_edges_do_not_loop(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = seed(conn)
        shape = tree_shape(conn, ids["powershell"], max_depth=10, include_inferred=True)
    assert shape.depth_by_id[ids["services"]] == -4
    assert ids["whoami"] in shape.depth_by_id
    assert len(shape.depth_by_id) == len(ids)


def test_tree_shape_respects_node_limit(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = seed(conn)
        shape = tree_shape(conn, ids["explorer"], max_depth=10, max_nodes=3)
    assert shape.truncated
    assert sum(1 for d in shape.depth_by_id.values() if d > 0) == 2


def test_migrations_are_idempotent_and_tamper_evident(dsn):
    assert migrate(dsn) == []
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert current_version(conn) == 2
        conn.execute("UPDATE schema_migrations SET checksum = 'x' WHERE version = 1")
    with pytest.raises(MigrationError):
        migrate(dsn)


def test_single_verified_parent_and_no_self_loop(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = seed(conn)
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO process_edges (parent_id, child_id, level, rule_ver) VALUES (%s, %s, 'VERIFIED', 't')",
                (ids["services"], ids["powershell"]),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO process_edges (parent_id, child_id, level, rule_ver) VALUES (%s, %s, 'WEAK', 't')",
                (ids["cmd"], ids["cmd"]),
            )
