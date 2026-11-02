import asyncio
import re
import secrets
from contextlib import contextmanager
from functools import cache
from pathlib import Path

import httpx
import yaml
from jsonschema import Draft202012Validator

from ..app import create_app
from ..db import Database

SPEC_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"
METHODS = {"get", "post", "put", "patch", "delete"}


@cache
def spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


@cache
def operations() -> dict[str, dict]:
    ops = {}
    for path, item in spec()["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                ops[op["operationId"]] = {**op, "method": method.upper(), "path": path}
    return ops


def _deref(node: dict) -> dict:
    while "$ref" in node:
        target = spec()
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _path_regex(template: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", template) + "$")


def assert_conforms(response: httpx.Response, operation_id: str) -> None:
    op = operations().get(operation_id)
    if op is None:
        raise AssertionError(f"Unknown operationId {operation_id}")
    request = response.request
    if request.method != op["method"] or not _path_regex(op["path"]).match(request.url.path):
        raise AssertionError(f"{operation_id} is {op['method']} {op['path']}, got {request.method} {request.url.path}")
    status = str(response.status_code)
    documented = op.get("responses", {})
    entry = documented.get(status) or documented.get(status[0] + "XX") or documented.get("default")
    if entry is None:
        raise AssertionError(f"{operation_id}: status {status} is not in the contract {sorted(documented)}; body: {response.text[:300]}")
    content = _deref(entry).get("content")
    if not content:
        if response.content:
            raise AssertionError(f"{operation_id}: status {status} must have an empty body")
        return
    schema = content.get("application/json", {}).get("schema")
    if schema is None:
        return
    validator = Draft202012Validator({**schema, "components": spec()["components"]})
    errors = sorted(validator.iter_errors(response.json()), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f"  {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors[:10]]
        raise AssertionError(f"{operation_id} {status} does not match the contract:\n" + "\n".join(lines))


class EngineClient:
    def __init__(self, app):
        self.app = app
        self.headers = {"Authorization": f"Bearer {app.state.session_token}"}

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async def run() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://engine", headers=self.headers) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(run())

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)


@contextmanager
def engine_client(dsn: str | None, services=None):
    db = Database(dsn) if dsn else None
    if db:
        db.open()
    try:
        yield EngineClient(create_app(secrets.token_urlsafe(32), db, services))
    finally:
        if db:
            db.close()
