import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from elasticsearch import Elasticsearch

from ..elk import queries
from ..elk.client import make_client
from ..elk.errors import classify_exception

DEFAULT_PATTERNS = {"log": ["logs-windows.sysmon_operational-*"], "alert": [".alerts-security.alerts-default"]}
TIME_FIELDS = {"log": "event.ingested", "alert": "@timestamp"}


def export_hits(
    es: Elasticsearch, patterns: list[str], time_field: str, lower: datetime, upper: datetime, limit: int, page_size: int = 1000
) -> list[dict]:
    pit = es.open_point_in_time(index=",".join(patterns), **queries.open_pit_params())["id"]
    hits, after = [], None
    try:
        while len(hits) < limit:
            resp = es.search(**queries.window_search(pit, time_field, lower, upper, min(page_size, limit - len(hits)), after))
            if queries.is_partial(resp.body):
                raise RuntimeError("Elasticsearch returned partial results; retry later")
            batch = resp["hits"]["hits"]
            if not batch:
                break
            hits.extend({"_index": h["_index"], "_id": h["_id"], "_source": h["_source"]} for h in batch)
            after = batch[-1]["sort"]
            pit = resp.get("pit_id", pit)
    finally:
        es.close_point_in_time(id=pit)
    return hits


def write_ndjson(hits: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for hit in hits:
            f.write(json.dumps(hit, ensure_ascii=False) + "\n")


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ir_engine.tools.export_hits", description="Xuất document thật từ ELK thành NDJSON để nạp bằng 'fixtures load'"
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--ca-cert", type=Path)
    parser.add_argument("--ca-fingerprint")
    parser.add_argument("--kind", choices=["log", "alert"], default="log")
    parser.add_argument("--pattern", action="append")
    parser.add_argument("--from", dest="lower", type=_ts)
    parser.add_argument("--to", dest="upper", type=_ts)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    upper = args.upper or datetime.now(timezone.utc)
    lower = args.lower or upper - timedelta(hours=24)
    api_key = os.environ.get("IR_ELK_API_KEY") or getpass.getpass("API key (encoded): ")
    es = make_client(
        args.url,
        api_key,
        ca_pem=args.ca_cert.read_text(encoding="utf-8") if args.ca_cert else None,
        ca_fingerprint=args.ca_fingerprint,
        max_retries=0,
    )
    try:
        hits = export_hits(es, args.pattern or DEFAULT_PATTERNS[args.kind], TIME_FIELDS[args.kind], lower, upper, args.limit)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": classify_exception(exc).value, "message": str(exc)}, ensure_ascii=False))
        return 2
    write_ndjson(hits, args.out)
    print(json.dumps({"ok": True, "written": len(hits), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
