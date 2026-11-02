import hashlib
import re
from datetime import datetime, timezone

INDEX_PREFIXES = ("partial-", "restored-")
_NANOS = re.compile(r"(\.\d{6})\d+")


def get_path(doc: dict, path: str):
    if not isinstance(doc, dict):
        return None
    if path in doc:
        return doc[path]
    parts = path.split(".")
    for i in range(1, len(parts)):
        head = ".".join(parts[:i])
        child = doc.get(head)
        if isinstance(child, dict):
            found = get_path(child, ".".join(parts[i:]))
            if found is not None:
                return found
    return None


def normalize_index(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in INDEX_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):]
                changed = True
    return name


def parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    text = _NANOS.sub(r"\1", str(value).replace("Z", "+00:00"))
    ts = datetime.fromisoformat(text)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def normalize_guid(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("{}").lower()


def host_key(src: dict) -> str | None:
    for prefix, path in (("id", "host.id"), ("agent", "agent.id"), ("name", "host.name")):
        value = get_path(src, path)
        if value:
            return f"{prefix}:{str(value).lower()}"
    return None


def canonical_key(src: dict, hkey: str | None) -> str | None:
    record_id = get_path(src, "winlog.record_id")
    channel = get_path(src, "winlog.channel")
    if not (hkey and record_id is not None and channel):
        return None
    raw = f"{hkey}|{str(channel).lower()}|{record_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def cluster_key(cluster_uuid: str | None, cluster_name: str | None) -> str:
    if cluster_uuid and cluster_uuid != "_na_":
        return cluster_uuid
    if not cluster_name:
        raise ValueError("cluster_name is required when cluster_uuid is unavailable")
    return "name:" + re.sub(r"[^a-z0-9_-]+", "-", cluster_name.lower()).strip("-")
