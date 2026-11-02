from datetime import datetime, timezone
from pathlib import Path

import psycopg

from ..db.migrate import current_version, discover, migrate
from .postgres import DATABASE, EmbeddedPostgres

KEEP_BACKUPS = 3


class UpgradeError(RuntimeError):
    pass


def pending_versions(dsn: str) -> tuple[int | None, list[int]]:
    with psycopg.connect(dsn) as conn:
        applied = current_version(conn)
    return applied, [m.version for m in discover() if applied is None or m.version > applied]


def prune(backups_dir: Path, keep: int = KEEP_BACKUPS) -> None:
    dumps = sorted(backups_dir.glob("irdesktop-v*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dumps[keep:]:
        old.unlink(missing_ok=True)


def backup_before_migrate(pg: EmbeddedPostgres, password: str, dsn: str, backups_dir: Path) -> Path | None:
    """pg_dump an existing database before applying new migrations (report 9.1 step 6). A fresh database needs no backup."""
    applied, pending = pending_versions(dsn)
    if applied is None or not pending:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dbname = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname") or DATABASE
    out = pg.dump(password, backups_dir / f"irdesktop-v{applied:04d}-{stamp}.dump", dbname=dbname)
    prune(backups_dir)
    return out


def migrate_with_backup(pg: EmbeddedPostgres, password: str, dsn: str, backups_dir: Path) -> Path | None:
    backup = backup_before_migrate(pg, password, dsn, backups_dir)
    try:
        migrate(dsn)
    except Exception as exc:
        hint = f'; khôi phục bằng: ir-engine backup restore --file "{backup}"' if backup else ""
        raise UpgradeError(f"Migration failed: {exc}{hint}") from exc
    return backup
