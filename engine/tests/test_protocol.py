import json
import secrets
import subprocess
import sys

import httpx

from ir_engine.runtime.postgres import find_bin_dir


def start(env, args, stdin_line=None):
    proc = subprocess.Popen(
        [sys.executable, "-m", "ir_engine", "serve", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    if stdin_line is not None:
        proc.stdin.write(stdin_line + "\n")
        proc.stdin.flush()
    line = proc.stdout.readline()
    return proc, json.loads(line) if line else None


def stop(proc, timeout=30):
    proc.stdin.close()
    return proc.wait(timeout=timeout)


def test_dev_mode_ready_auth_and_stub(engine_env):
    proc, msg = start(engine_env, ["--dev", "--watch-stdin"])
    try:
        assert msg["event"] == "ready" and msg["contract_version"] == "1.0.0"
        base = f"http://127.0.0.1:{msg['port']}"
        token = engine_env["IR_DEV_TOKEN"]

        unauthorized = httpx.get(f"{base}/system/health")
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == "ENGINE_UNAUTHORIZED"

        health = httpx.get(f"{base}/system/health", headers={"Authorization": f"Bearer {token}"}).json()
        assert health["status"] == "ok"
        assert health["db"] == {"status": "up", "schema_version": 2}
        assert set(health["workers"]) == {"sync", "correlation"}
        assert {w["state"] for w in health["workers"].values()} == {"running"}

        status = httpx.get(f"{base}/sync/status", headers={"Authorization": f"Bearer {token}"})
        assert status.status_code == 200 and status.json() == {"scheduler": "blocked", "sources": []}

        stub = httpx.get(f"{base}/trace/unresolved", headers={"Authorization": f"Bearer {token}"})
        assert stub.status_code == 501
        assert stub.json()["error"]["detail"] == {"owner": "TV2", "operation": "listUnresolved"}

        invalid = httpx.get(f"{base}/search/events", params={"type": "nope", "value": "x"}, headers={"Authorization": f"Bearer {token}"})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    finally:
        assert stop(proc) == 0


def test_production_mode_runs_embedded_postgres(engine_env, tmp_path):
    env = dict(engine_env)
    env.pop("IR_DATABASE_URL")
    env["IR_PG_BIN"] = str(find_bin_dir())
    token = secrets.token_hex(32)
    data_dir = tmp_path / "IRDesktop"
    proc, msg = start(env, [], json.dumps({"token": token, "data_dir": str(data_dir)}))
    try:
        assert msg and msg["event"] == "ready", proc.stderr.read() if msg is None else msg
        health = httpx.get(f"http://127.0.0.1:{msg['port']}/system/health", headers={"Authorization": f"Bearer {token}"}).json()
        assert health["db"]["status"] == "up"
        assert (data_dir / "pgdata" / "postmaster.pid").exists()
    finally:
        assert stop(proc, timeout=60) == 0
    assert not (data_dir / "pgdata" / "postmaster.pid").exists()


def test_shutdown_endpoint(engine_env):
    proc, msg = start(engine_env, ["--dev"])
    r = httpx.post(f"http://127.0.0.1:{msg['port']}/system/shutdown", headers={"Authorization": f"Bearer {engine_env['IR_DEV_TOKEN']}"})
    assert r.status_code == 202
    assert proc.wait(timeout=15) == 0


def test_bad_handshake_reports_error(engine_env):
    proc, msg = start(engine_env, [], "not-json")
    assert msg["event"] == "error" and msg["code"] == "VALIDATION_ERROR"
    assert proc.wait(timeout=10) == 2
