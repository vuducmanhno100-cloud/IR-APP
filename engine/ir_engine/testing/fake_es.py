import argparse
import fnmatch
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..ingest.identity import get_path, parse_ts

VERSION = "9.5.5"
ERROR_TYPES = {401: "security_exception", 403: "security_exception", 429: "es_rejected_execution_exception", 503: "unavailable_shards_exception"}


def _aliases(index: str, source: dict) -> list[str]:
    names = [index]
    ds = source.get("data_stream") if isinstance(source.get("data_stream"), dict) else None
    if ds and {"type", "dataset", "namespace"} <= ds.keys():
        names.append(f"{ds['type']}-{ds['dataset']}-{ds['namespace']}")
    match = re.match(r"^\.internal\.(alerts-.+?)-\d{6}$", index)
    if match:
        names.append("." + match.group(1))
    return names


@dataclass
class FakeState:
    api_key: str = "fake-api-key"
    api_key_id: str = "fake-key-id"
    api_key_name: str = "ir-desktop-test"
    username: str = "tester"
    cluster_name: str = "lab-cluster"
    cluster_uuid: str = "fake-cluster-uuid"
    monitor: bool = True
    readable: list[str] = field(default_factory=lambda: ["*"])
    docs: list[dict] = field(default_factory=list)
    pits: dict = field(default_factory=dict)
    failures: list[int] = field(default_factory=list)
    partial: bool = False
    requests: list[str] = field(default_factory=list)

    def add_hits(self, hits: list[dict]) -> None:
        self.docs.extend(hits)

    def fail_next(self, status: int, times: int = 1) -> None:
        self.failures.extend([status] * times)

    def can_read(self, pattern: str) -> bool:
        return any(fnmatch.fnmatchcase(pattern, r) or fnmatch.fnmatchcase(r, pattern) or r == "*" for r in self.readable)

    def resolve(self, target: str) -> list[dict]:
        patterns = [p for p in target.split(",") if p]
        out = []
        for doc in self.docs:
            names = _aliases(doc["_index"], doc["_source"])
            if any(fnmatch.fnmatchcase(n, p) for p in patterns for n in names) and any(
                self.can_read(n) for n in names
            ):
                out.append(doc)
        return out


def _has_field(value, path: str) -> bool:
    if isinstance(value, list):
        return any(_has_field(v, path) for v in value)
    if not isinstance(value, dict):
        return False
    if path in value:
        return value[path] is not None
    parts = path.split(".")
    for i in range(1, len(parts)):
        head = ".".join(parts[:i])
        if head in value and _has_field(value[head], ".".join(parts[i:])):
            return True
    return False


def _range_ok(doc: dict, query: dict) -> bool:
    filters = (((query or {}).get("bool") or {}).get("filter")) or []
    for f in filters:
        for fname, cond in (f.get("range") or {}).items():
            value = parse_ts(get_path(doc["_source"], fname))
            if value is None:
                return False
            if "gte" in cond and value < parse_ts(cond["gte"]):
                return False
            if "lt" in cond and value >= parse_ts(cond["lt"]):
                return False
    return True


def _sort_value(doc: dict, sort: list) -> list:
    out = []
    for spec in sort:
        (fname, _), = spec.items()
        if fname == "_shard_doc":
            out.append(doc["_shard_doc"])
        else:
            ts = parse_ts(get_path(doc["_source"], fname))
            out.append(int(ts.timestamp() * 1000))
    return out


class Handler(BaseHTTPRequestHandler):
    state: FakeState

    def log_message(self, *args):
        pass

    def _send(self, status: int, body) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Elastic-Product", "Elasticsearch")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, reason: str) -> None:
        self._send(status, {"error": {"type": ERROR_TYPES.get(status, "exception"), "reason": reason}, "status": status})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"ApiKey {self.state.api_key}"

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        path, query = unquote(url.path), parse_qs(url.query)
        st = self.state
        st.requests.append(f"{method} {path}")
        if not self._authorized():
            return self._error(401, "unable to authenticate with provided credentials")
        if st.failures:
            status = st.failures.pop(0)
            return self._error(status, f"injected {status}")
        body = self._body()
        if path == "/" and method in ("GET", "HEAD"):
            if not st.monitor:
                return self._error(403, "action [cluster:monitor/main] is unauthorized")
            return self._send(200, {
                "name": "fake-node", "cluster_name": st.cluster_name, "cluster_uuid": st.cluster_uuid,
                "version": {"number": VERSION, "build_flavor": "default"}, "tagline": "You Know, for Search",
            })
        if path == "/_security/_authenticate":
            return self._send(200, {
                "username": st.username, "authentication_type": "api_key",
                "api_key": {"id": st.api_key_id, "name": st.api_key_name},
            })
        if path == "/_security/user/_has_privileges":
            index = {}
            for spec in body.get("index", []):
                for name in spec["names"]:
                    index[name] = {p: st.can_read(name) for p in spec["privileges"]}
            cluster = {p: st.monitor for p in body.get("cluster", [])}
            ok = all(all(v.values()) for v in index.values()) and all(cluster.values())
            return self._send(200, {"username": st.username, "has_all_requested": ok, "cluster": cluster, "index": index, "application": {}})
        if path.endswith("/_field_caps"):
            target = path[1:].split("/")[0]
            docs = st.resolve(target)
            fields = {}
            raw = body.get("fields") or query.get("fields") or ["*"]
            wanted = ",".join(raw if isinstance(raw, list) else [raw]).split(",")
            for f in wanted:
                if any(_has_field(d["_source"], f) for d in docs):
                    fields[f] = {"keyword": {"type": "keyword", "searchable": True, "aggregatable": True}}
            return self._send(200, {"indices": sorted({d["_index"] for d in docs}), "fields": fields})
        if path.endswith("/_count"):
            target = path[1:].split("/")[0]
            docs = [d for d in st.resolve(target) if _range_ok(d, body.get("query"))]
            return self._send(200, {"count": len(docs), "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0}})
        if path.endswith("/_pit") and method == "POST":
            target = path[1:].split("/")[0]
            if not any(st.can_read(p) for p in target.split(",")):
                return self._error(403, f"action [indices:data/read/open_point_in_time] is unauthorized for {target}")
            snapshot = [dict(d, _shard_doc=i) for i, d in enumerate(st.resolve(target))]
            pit_id = uuid.uuid4().hex
            st.pits[pit_id] = snapshot
            return self._send(200, {"id": pit_id, "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0}})
        if path == "/_pit" and method == "DELETE":
            freed = 1 if st.pits.pop(body.get("id"), None) is not None else 0
            return self._send(200, {"succeeded": True, "num_freed": freed})
        if path == "/_search":
            pit = (body.get("pit") or {}).get("id")
            if pit not in st.pits:
                return self._error(404, "No search context found for id")
            sort = body.get("sort") or [{"_shard_doc": "asc"}]
            after = body.get("search_after")
            if after is not None and any(v is None for v in after):
                return self._error(400, "search_after does not accept null values")
            rows = [d for d in st.pits[pit] if _range_ok(d, body.get("query"))]
            rows.sort(key=lambda d: _sort_value(d, sort))
            if after is not None:
                rows = [d for d in rows if _sort_value(d, sort) > list(after)]
            size = body.get("size", 10)
            hits = [{"_index": d["_index"], "_id": d["_id"], "_source": d["_source"], "sort": _sort_value(d, sort)} for d in rows[:size]]
            shards = {"total": 2, "successful": 1 if st.partial else 2, "skipped": 0, "failed": 1 if st.partial else 0}
            return self._send(200, {"pit_id": pit, "took": 1, "timed_out": False, "_shards": shards, "hits": {"hits": hits}})
        return self._error(400, f"fake ES does not support {method} {path}")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_HEAD(self):
        self._handle("HEAD")


class FakeElasticsearch:
    def __init__(self, state: FakeState | None = None, port: int = 0):
        self.state = state or FakeState()
        handler = type("BoundHandler", (Handler,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def load_ndjson(self, path: Path) -> None:
        files = sorted(f for f in path.rglob("*.ndjson") if "lab" not in f.relative_to(path).parts) if path.is_dir() else [path]
        for f in files:
            self.state.add_hits([json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()])


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake Elasticsearch 9.x for local development (HTTP only)")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--api-key", default="fake-api-key")
    parser.add_argument("--fixtures", type=Path, action="append", default=[])
    args = parser.parse_args()
    fake = FakeElasticsearch(FakeState(api_key=args.api_key), port=args.port)
    for p in args.fixtures:
        fake.load_ndjson(p)
    print(json.dumps({"url": fake.url, "api_key": args.api_key, "documents": len(fake.state.docs)}), flush=True)
    with fake:
        fake.thread.join()


if __name__ == "__main__":
    main()
