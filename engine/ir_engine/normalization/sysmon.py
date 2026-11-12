from dataclasses import dataclass
from ipaddress import ip_address
from uuid import UUID

from ..ingest.identity import get_path


@dataclass(frozen=True)
class Normalized:
    code: str
    guid: str | None
    parent_guid: str | None
    fields: dict
    parent_fields: dict
    terms: tuple[tuple[str, str], ...]
    supported: bool


def guid(value):
    try:
        return str(UUID(str(value))) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


def normalize(src: dict) -> Normalized:
    def get(path):
        return get_path(src, path)

    provider = get("winlog.provider_name") or get("event.provider")
    supported = provider == "Microsoft-Windows-Sysmon" or get("event.dataset") == "windows.sysmon_operational"
    code = str(get("event.code") or "")
    terms = set()

    def add(kind, value):
        if isinstance(value, list):
            for item in value:
                add(kind, item)
        elif value is not None and str(value).strip():
            text = str(value).strip().lower()
            if kind == "domain":
                text = text.rstrip(".")
            elif kind == "process_guid":
                text = guid(text)
            elif kind == "ip":
                try:
                    text = str(ip_address(text))
                except ValueError:
                    return
            if text:
                terms.add((kind, text))

    for kind, paths in {
        "host": ["host.name"],
        "event_code": ["event.code"],
        "process_guid": ["process.entity_id", "process.parent.entity_id"],
        "image": ["process.executable", "process.name", "process.parent.executable", "process.parent.name"],
        "command_line": ["process.command_line"],
        "domain": ["dns.question.name", "source.domain", "destination.domain"],
        "ip": ["source.ip", "destination.ip", "dns.resolved_ip"],
        "hash": ["process.hash.md5", "process.hash.sha1", "process.hash.sha256", "process.pe.imphash"],
    }.items():
        for path in paths:
            add(kind, get(path))
    user = get("user.name")
    domain = get("user.domain")
    if user:
        add("user", f"{domain}\\{user}" if domain else user)

    def fields(prefix):
        result = {
            k: get(f"{prefix}.{v}")
            for k, v in {"pid": "pid", "image": "executable", "name": "name", "command_line": "command_line"}.items()
        }
        if any(value is not None and not isinstance(value, str) for key, value in result.items() if key != "pid"):
            raise ValueError("Process text fields must be strings")
        pid = result["pid"]
        try:
            result["pid"] = int(pid) if pid is not None else None
            if result["pid"] is not None and not 0 <= result["pid"] <= 2147483647:
                result["pid"] = None
        except (TypeError, ValueError):
            result["pid"] = None
        return result

    own, parent = fields("process"), fields("process.parent")
    own.update(user_name=f"{domain}\\{user}" if domain and user else user, integrity_level=get("winlog.event_data.IntegrityLevel"))
    parent["user_name"] = get("winlog.event_data.ParentUser")
    return Normalized(
        code, guid(get("process.entity_id")), guid(get("process.parent.entity_id")), own, parent, tuple(sorted(terms)), supported
    )
