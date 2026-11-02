import re

import psycopg
from elasticsearch import Elasticsearch

from ..elk import probe
from ..ingest.identity import normalize_index
from . import config
from .runner import Access

_DATA_STREAM_BACKING = re.compile(r"^\.ds-(.+)-\d{4}\.\d{2}\.\d{2}-\d{6}$")
_ROLLOVER = re.compile(r"^(.+)-\d{6}$")


def stream_key(index: str) -> str:
    """Backing indices come and go with rollover and ILM; compare the data stream or alias they belong to."""
    name = normalize_index(index)
    for pattern in (_DATA_STREAM_BACKING, _ROLLOVER):
        match = pattern.match(name)
        if match:
            return match.group(1)
    return name


def visible_streams(es: Elasticsearch, source: dict) -> set[str]:
    caps = es.field_caps(
        index=",".join(source["index_patterns"]), fields=source["timestamp_field"], ignore_unavailable=True, allow_no_indices=True
    )
    return {stream_key(name) for name in caps.get("indices", [])}


def check(conn: psycopg.Connection, es: Elasticsearch, source: dict) -> Access:
    """Run before each incremental window (report 3.4).

    Wildcard patterns silently expand only to what the key may read, so a narrowed key keeps returning 200 with less data.
    Pattern-level loss is caught by _has_privileges; narrower loss shows up as a data stream that stops being visible."""
    missing = probe.missing_privileges(probe.privileges(es, list(source["index_patterns"])))
    if missing:
        return Access(denied=f"API key không còn quyền read/view_index_metadata trên: {', '.join(missing)}")
    current = visible_streams(es, source)
    key = config.streams_key(source["id"])
    baseline = set(config.get_value(conn, key, []))
    config.set_value(conn, key, sorted(baseline | current))
    lost = sorted(baseline - current)
    if lost:
        return Access(
            warning=(
                f"Không còn thấy {', '.join(lost)} trong phạm vi của API key; dữ liệu có thể thiếu. "
                "Nếu ELK đã xóa chúng, lưu lại cấu hình nguồn để xác nhận."
            )
        )
    return Access()


def recheck_forbidden(es: Elasticsearch, source: dict) -> bool:
    return not probe.missing_privileges(probe.privileges(es, list(source["index_patterns"])))
