"""Start the packaged engine the way the desktop app does and check it end to end, before the installer is built.

The engine gets a token and a data folder on stdin, starts the bundled PostgreSQL (initdb on first run), migrates and
prints its ready line on stdout; closing stdin shuts both down. PATH is cut down to Windows itself and PG*/IR_*
variables are dropped, so a PostgreSQL or DLL installed on the build machine cannot stand in for the bundled one.

On Windows the engine keeps its PostgreSQL password in Credential Manager (IRDesktop / postgres-superuser), the
same entry the installed app uses, so the frozen keyring backend is exercised too.

Usage: python smoke_engine.py <path to ir-engine(.exe)> [--memory-secrets]   (standard library only)
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

READY_TIMEOUT = 180  # first run includes initdb, slow on Windows with antivirus scanning new files
STOP_TIMEOUT = 60


def clean_env(data: Path, memory_secrets: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("IR_", "PG"))}
    if memory_secrets:
        env["IR_SECRET_BACKEND"] = "memory"
    if os.name == "nt":
        windows = os.environ.get("SystemRoot", r"C:\Windows")
        env["PATH"] = os.pathsep.join([rf"{windows}\System32", windows, rf"{windows}\System32\Wbem"])
    else:
        env["PATH"] = "/usr/bin:/bin"
        env["HOME"] = str(data)
    return env


def read_ready(proc: subprocess.Popen) -> dict | None:
    """First JSON line with event=ready, or None when the engine exits or the timeout passes.

    The reader keeps draining stdout afterwards so the engine never blocks on a full pipe."""
    found: list[dict] = []
    done = threading.Event()

    def reader():
        for line in proc.stdout:
            if not found and line.startswith("{"):
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get("event") == "ready":
                    found.append(message)
                    done.set()
        done.set()

    threading.Thread(target=reader, daemon=True).start()
    done.wait(READY_TIMEOUT)
    return found[0] if found else None


def get(port: int, token: str, path: str) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=15) as response:
        return json.load(response)


def main(exe: Path, memory_secrets: bool = False) -> int:
    if not exe.exists():
        print(f"ERROR: {exe} not found", file=sys.stderr)
        return 1
    data = Path(tempfile.mkdtemp(prefix="ir-smoke-"))
    token = secrets.token_hex(32)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    started = time.monotonic()
    proc = subprocess.Popen(
        [str(exe), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=clean_env(data, memory_secrets),
        creationflags=flags,
    )
    ok = False
    try:
        proc.stdin.write(json.dumps({"token": token, "data_dir": str(data / "IRDesktop")}) + "\n")
        proc.stdin.flush()
        ready = read_ready(proc)
        if not ready:
            print(f"ERROR: engine sent no ready line within {READY_TIMEOUT}s (exit code {proc.poll()})", file=sys.stderr)
            return 1
        print(f"engine ready in {time.monotonic() - started:.1f}s (version {ready.get('version')})")
        health = get(ready["port"], token, "/system/health")
        db = health.get("db", {})
        print(f"health {health.get('status')}, database {db.get('status')} (schema {db.get('schema_version')})")
        pid_file = data / "IRDesktop" / "pgdata" / "postmaster.pid"
        if health.get("status") != "ok" or not pid_file.exists():
            print("ERROR: engine is not healthy or the bundled PostgreSQL is not running", file=sys.stderr)
            return 1
        proc.stdin.close()  # the app closing its end is the shutdown signal
        code = proc.wait(STOP_TIMEOUT)
        if code != 0 or pid_file.exists():
            print(f"ERROR: engine exit code {code}; PostgreSQL still running: {pid_file.exists()}", file=sys.stderr)
            return 1
        print("engine stopped and PostgreSQL shut down cleanly")
        ok = True
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
        logs = data / "IRDesktop" / "logs"
        if not ok and logs.is_dir():
            for log in sorted(logs.glob("*.log")):
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
                print(f"--- last lines of {log.name}:\n" + "\n".join(tail), file=sys.stderr)
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("engine", type=Path, help="path to ir-engine(.exe) inside dist/engine")
    parser.add_argument("--memory-secrets", action="store_true", help="for Linux CI without a keyring; leave off on Windows")
    args = parser.parse_args()
    sys.exit(main(args.engine, args.memory_secrets))
