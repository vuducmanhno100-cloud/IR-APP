import json
import re

import psycopg
import yaml
from openapi_spec_validator import validate

from ir_engine.app import create_app
from ir_engine.contracts import CONTRACT_VERSION, ENUMS, ERROR_HTTP, ErrorCode

from .conftest import CONTRACTS

SPEC = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
ENUM_FILE = json.loads((CONTRACTS / "enums.json").read_text(encoding="utf-8"))
METHODS = {"get", "post", "put", "patch", "delete"}

SCHEMA_ENUMS = {
    "ConnectionState": "connection_state",
    "SourceKind": "source_kind",
    "SourceState": "source_state",
    "TimestampStrategy": "timestamp_strategy",
    "RunType": "run_type",
    "RunStatus": "run_status",
    "ProcessState": "process_state",
    "EdgeLevel": "edge_level",
    "LinkType": "link_type",
    "AttrSource": "attr_source",
    "TermType": "term_type",
    "MatchMode": "match_mode",
    "Completeness": "completeness",
    "CaseStatus": "case_status",
    "CaseItemType": "case_item_type",
    "AlertStatus": "alert_status",
    "AlertSeverity": "alert_severity",
}

COLUMN_ENUMS = {
    ("elk_connections", "state"): "connection_state",
    ("source_configs", "kind"): "source_kind",
    ("source_configs", "state"): "source_state",
    ("source_configs", "timestamp_strategy"): "timestamp_strategy",
    ("sync_runs", "run_type"): "run_type",
    ("sync_runs", "status"): "run_status",
    ("raw_events", "process_state"): "process_state",
    ("process_edges", "level"): "edge_level",
    ("event_process_links", "level"): "edge_level",
    ("event_process_links", "link_type"): "link_type",
    ("unresolved_relations", "status"): "unresolved_status",
    ("unresolved_relations", "relation"): "unresolved_relation",
    ("process_instances", "attr_source"): "attr_source",
    ("event_search_terms", "term_type"): "term_type",
    ("incident_cases", "status"): "case_status",
    ("case_items", "item_type"): "case_item_type",
    ("alert_records", "status"): "alert_status",
    ("alert_records", "severity"): "alert_severity",
}


def test_openapi_is_valid():
    validate(SPEC)
    assert SPEC["info"]["version"] == CONTRACT_VERSION == ENUM_FILE["contract_version"]


def test_routes_match_openapi():
    generated = create_app("x" * 32, None).openapi()
    implemented = {(m, p) for p, ops in generated["paths"].items() for m in ops if m in METHODS}
    specified = {(m, p) for p, ops in SPEC["paths"].items() for m in ops if m in METHODS}
    assert implemented == specified


def test_every_operation_has_owner():
    for path, ops in SPEC["paths"].items():
        for method, op in ops.items():
            if method in METHODS:
                assert op.get("x-owner") in {"TV1", "TV2"}, (method, path)


def test_python_enums_match_enum_file():
    for key, enum_cls in ENUMS.items():
        assert [e.value for e in enum_cls] == ENUM_FILE[key], key
    assert {c.value for c in ErrorCode} == set(ENUM_FILE["error_codes"])
    for code, (status, retryable) in ERROR_HTTP.items():
        assert ENUM_FILE["error_codes"][code.value] == {"http": status, "retryable": retryable}


def test_openapi_enums_match_enum_file():
    schemas = SPEC["components"]["schemas"]
    for schema_name, key in SCHEMA_ENUMS.items():
        assert schemas[schema_name]["enum"] == ENUM_FILE[key], schema_name
    assert schemas["ErrorCode"]["enum"] == list(ENUM_FILE["error_codes"])


def test_database_checks_match_enum_file(dsn):
    query = """
        SELECT pg_get_constraintdef(c.oid) AS def
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
        WHERE c.contype = 'c' AND t.relname = %s AND a.attname = %s
    """
    with psycopg.connect(dsn) as conn:
        for (table, column), key in COLUMN_ENUMS.items():
            defs = [r[0] for r in conn.execute(query, (table, column)).fetchall()]
            lists = [re.findall(r"'([^']+)'::text", d) for d in defs if "ARRAY[" in d]
            assert lists, (table, column)
            assert set(lists[0]) == set(ENUM_FILE[key]), (table, column)


def test_cors_allows_only_ui_origins():
    import asyncio

    import httpx

    async def run():
        transport = httpx.ASGITransport(app=create_app("x" * 32, None))
        async with httpx.AsyncClient(transport=transport, base_url="http://engine") as client:
            preflight = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
            ok = await client.options("/system/health", headers={"Origin": "http://tauri.localhost", **preflight})
            evil = await client.options("/system/health", headers={"Origin": "https://evil.example", **preflight})
            plain = await client.get("/system/health", headers={"Origin": "http://tauri.localhost"})
            return ok, evil, plain

    ok, evil, plain = asyncio.run(run())
    assert ok.headers.get("access-control-allow-origin") == "http://tauri.localhost"
    assert "access-control-allow-origin" not in evil.headers
    assert plain.status_code == 401
