import hashlib
import re
from dataclasses import dataclass
from importlib import resources

import psycopg

FILE_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def discover() -> list[Migration]:
    found = []
    for entry in resources.files("ir_engine.db.migrations").iterdir():
        match = FILE_PATTERN.match(entry.name)
        if match:
            found.append(Migration(int(match.group(1)), match.group(2), entry.read_text(encoding="utf-8")))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise MigrationError(f"Migration versions must be contiguous from 1, got {versions}")
    return found


def _ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     integer PRIMARY KEY,
            name        text NOT NULL,
            checksum    text NOT NULL,
            applied_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def current_version(conn: psycopg.Connection) -> int | None:
    exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL AS ok").fetchone()
    if not (exists["ok"] if isinstance(exists, dict) else exists[0]):
        return None
    row = conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()
    return row["v"] if isinstance(row, dict) else row[0]


def migrate(dsn: str) -> list[int]:
    applied_now = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        _ensure_table(conn)
        applied = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()}
        for m in discover():
            if m.version in applied:
                if applied[m.version] != m.checksum:
                    raise MigrationError(f"Migration {m.version:04d}_{m.name} was modified after being applied")
                continue
            with conn.transaction():
                conn.execute(m.sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (m.version, m.name, m.checksum),
                )
            applied_now.append(m.version)
    return applied_now
