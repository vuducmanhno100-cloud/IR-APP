import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ir_engine.elk import queries  # noqa: E402

T0 = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)
OUT = Path(__file__).resolve().parents[2] / "contracts" / "elk" / "examples.json"

examples = {
    "api_key_request": queries.api_key_request(
        "ir-desktop-<ten-thanh-vien>",
        ["logs-windows.sysmon_operational-*"],
        [".alerts-security.alerts-*", ".internal.alerts-security.alerts-*"],
    ),
    "has_privileges": queries.has_privileges_body(["logs-windows.sysmon_operational-*", ".alerts-security.alerts-*"]),
    "open_pit_params": queries.open_pit_params(),
    "incremental_search": queries.window_search(
        "<pit_id>", "event.ingested", T0 - timedelta(seconds=120), T0 + timedelta(minutes=4), 1000
    ),
    "alert_status_search": queries.alert_status_search("<pit_id>", T0 - timedelta(hours=1), T0, 500),
    "field_caps": {"log": queries.LOG_REQUIRED_FIELDS, "alert": queries.ALERT_REQUIRED_FIELDS},
}

OUT.write_text(json.dumps(examples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(OUT)
