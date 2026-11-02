import argparse
import getpass
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from elasticsearch import ApiError, Elasticsearch

from ..elk.client import make_client
from ..elk.errors import classify_exception
from ..elk.queries import ALERT_REQUIRED_FIELDS, LOG_PRIVILEGES, LOG_REQUIRED_FIELDS


@dataclass
class Check:
    name: str
    ok: bool
    required: bool
    detail: str
    hint: str = ""


def _fields_present(es: Elasticsearch, patterns: list[str], wanted: list[str]) -> list[str]:
    caps = es.field_caps(index=",".join(patterns), fields=",".join(wanted), ignore_unavailable=True, allow_no_indices=True)
    return [f for f in wanted if f not in caps.get("fields", {})]


def run_checks(es: Elasticsearch, log_patterns: list[str], alert_patterns: list[str]) -> list[Check]:
    checks: list[Check] = []
    try:
        info = es.info()
        version = info["version"]["number"]
        checks.append(Check("elasticsearch_9x", version.startswith("9."), True, f"version {version}, cluster_uuid {info.get('cluster_uuid')}",
                            "Thiết kế dành cho Elasticsearch 9.x; OpenSearch/Wazuh indexer không dùng được API key kiểu Elasticsearch"))
    except ApiError as exc:
        if exc.meta.status == 403:
            checks.append(Check("elasticsearch_9x", True, False, "key không có quyền monitor; không đọc được version/cluster_uuid",
                                "Cấp thêm cluster privilege monitor hoặc nhập cluster_name trong wizard"))
        else:
            raise
    auth = es.security.authenticate()
    is_key = auth.get("authentication_type") == "api_key"
    checks.append(Check("api_key_auth", is_key, True, f"user {auth.get('username')}, key {auth.get('api_key', {}).get('name')}",
                        "Phải đăng nhập bằng API key, không dùng user/password"))
    priv = es.security.has_privileges(index=[{"names": log_patterns + alert_patterns, "privileges": LOG_PRIVILEGES}])
    missing = [f"{name}:{p}" for name, ps in priv["index"].items() for p, ok in ps.items() if not ok]
    checks.append(Check("index_privileges", not missing, True, "đủ quyền" if not missing else "thiếu " + ", ".join(missing),
                        "Tạo lại key theo contracts/elk/examples.json (api_key_request)"))
    log_count = es.count(index=",".join(log_patterns), ignore_unavailable=True, allow_no_indices=True)["count"]
    checks.append(Check("sysmon_data_present", log_count > 0, True, f"{log_count} document",
                        "Kiểm tra Elastic Agent đã cài Windows integration và bật kênh Sysmon"))
    missing_log = _fields_present(es, log_patterns, LOG_REQUIRED_FIELDS)
    checks.append(Check("sysmon_fields", not missing_log, True, "đủ trường" if not missing_log else "thiếu " + ", ".join(missing_log),
                        "Thiếu event.ingested: dùng timestamp_strategy EVENT_TIME hoặc nhờ thêm ingest pipeline; thiếu process.*: có thể đang dùng Winlogbeat"))
    alert_count = es.count(index=",".join(alert_patterns), ignore_unavailable=True, allow_no_indices=True)["count"]
    checks.append(Check("alerts_present", alert_count > 0, False, f"{alert_count} alert",
                        "Chưa có alert: bật detection rules trong Kibana Security hoặc kiểm tra tên space"))
    if alert_count:
        missing_alert = _fields_present(es, alert_patterns, ALERT_REQUIRED_FIELDS)
        checks.append(Check("alert_fields", not missing_alert, False, "đủ trường" if not missing_alert else "thiếu " + ", ".join(missing_alert)))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ir_engine.tools.lab_check", description="Kiểm tra ELK lab trước khi code đồng bộ")
    parser.add_argument("--url", required=True)
    parser.add_argument("--ca-cert", type=Path)
    parser.add_argument("--ca-fingerprint")
    parser.add_argument("--log-pattern", action="append", default=None)
    parser.add_argument("--alert-pattern", action="append", default=None)
    args = parser.parse_args(argv)
    api_key = os.environ.get("IR_ELK_API_KEY") or getpass.getpass("API key (encoded): ")
    es = make_client(
        args.url,
        api_key,
        ca_pem=args.ca_cert.read_text(encoding="utf-8") if args.ca_cert else None,
        ca_fingerprint=args.ca_fingerprint,
        max_retries=0,
    )
    log_patterns = args.log_pattern or ["logs-windows.sysmon_operational-*"]
    alert_patterns = args.alert_pattern or [".alerts-security.alerts-default"]
    try:
        checks = run_checks(es, log_patterns, alert_patterns)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": classify_exception(exc).value, "message": str(exc)}, ensure_ascii=False))
        return 2
    ok = all(c.ok for c in checks if c.required)
    print(json.dumps({"ok": ok, "checks": [asdict(c) for c in checks]}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
