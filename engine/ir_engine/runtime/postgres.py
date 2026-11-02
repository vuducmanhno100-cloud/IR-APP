import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg

SUPERUSER = "ir"
DATABASE = "irdesktop"


class PostgresError(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_bin_dir(explicit: str | None = None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("IR_PG_BIN"):
        candidates.append(Path(os.environ["IR_PG_BIN"]))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent.parent / "pgsql" / "bin")
    candidates.extend(sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True))
    suffix = ".exe" if os.name == "nt" else ""
    for c in candidates:
        if (c / f"pg_ctl{suffix}").exists():
            return c
    raise PostgresError("PostgreSQL binaries not found; set IR_PG_BIN")


class EmbeddedPostgres:
    def __init__(self, bin_dir: Path, data_root: Path):
        self.bin_dir = Path(bin_dir)
        self.data_dir = Path(data_root) / "pgdata"
        self.log_file = Path(data_root) / "logs" / "postgres.log"
        self.port: int | None = None

    def _tool(self, name: str) -> str:
        suffix = ".exe" if os.name == "nt" else ""
        return str(self.bin_dir / f"{name}{suffix}")

    def _run(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=flags)
        if result.returncode != 0:
            raise PostgresError(f"{Path(args[0]).name} failed: {result.stderr.strip() or result.stdout.strip()}")
        return result

    @property
    def initialized(self) -> bool:
        return (self.data_dir / "PG_VERSION").exists()

    def init(self, password: str) -> None:
        if self.initialized:
            return
        self.data_dir.parent.mkdir(parents=True, exist_ok=True)
        fd, pwfile = tempfile.mkstemp(prefix="irpw", dir=self.data_dir.parent)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(password)
            self._run(
                self._tool("initdb"),
                "-D", str(self.data_dir),
                "-U", SUPERUSER,
                "--auth=scram-sha-256",
                f"--pwfile={pwfile}",
                "--encoding=UTF8",
                "--no-locale",
            )
        finally:
            os.unlink(pwfile)

    def start(self, attempts: int = 3) -> int:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        last_error = None
        for _ in range(attempts):
            port = free_port()
            options = f"-p {port} -c listen_addresses=127.0.0.1"
            if os.name != "nt":
                options += " -c unix_socket_directories=''"
            try:
                self._run(self._tool("pg_ctl"), "-D", str(self.data_dir), "-l", str(self.log_file), "-o", options, "-w", "-t", "60", "start")
                self.port = port
                return port
            except PostgresError as exc:
                last_error = exc
        raise PostgresError(f"Could not start PostgreSQL: {last_error}")

    def stop(self) -> None:
        if not (self.data_dir / "postmaster.pid").exists():
            return
        self._run(self._tool("pg_ctl"), "-D", str(self.data_dir), "-m", "fast", "-w", "-t", "60", "stop")
        self.port = None

    def dsn(self, password: str, dbname: str = DATABASE) -> str:
        if self.port is None:
            raise PostgresError("PostgreSQL is not running")
        return psycopg.conninfo.make_conninfo(host="127.0.0.1", port=self.port, user=SUPERUSER, password=password, dbname=dbname)

    def ensure_database(self, password: str) -> str:
        with psycopg.connect(self.dsn(password, "postgres"), autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{DATABASE}"')
        return self.dsn(password)
