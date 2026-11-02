import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import logs, protocol
from .app import create_app
from .contracts import ErrorCode
from .credentials import PG_PASSWORD, get_or_create, shared_store
from .db import Database
from .db.migrate import current_version, migrate
from .runtime.postgres import EmbeddedPostgres, PostgresError, find_bin_dir
from .runtime.upgrade import migrate_with_backup
from .runtime.workers import WORKER_CHOICES, build_services
from .server import serve

log = logging.getLogger("ir_engine")


def default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "IRDesktop"
    return Path.home() / ".local" / "share" / "IRDesktop"


def dev_data_dir() -> Path:
    return Path(os.environ["IR_DATA_DIR"]) if os.environ.get("IR_DATA_DIR") else default_data_dir()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is required")
    return value


def cmd_serve(args: argparse.Namespace) -> int:
    if args.dev:
        return _serve_dev(args)
    try:
        handshake = protocol.read_handshake()
    except protocol.HandshakeError as exc:
        protocol.emit_error(ErrorCode.VALIDATION_ERROR.value, str(exc))
        return 2
    data_dir = Path(handshake.data_dir) if handshake.data_dir else default_data_dir()
    logs.setup(handshake.log_level, data_dir / "logs")
    pg = None
    db = None
    try:
        store = shared_store()
        password = get_or_create(store, PG_PASSWORD)
        pg = EmbeddedPostgres(find_bin_dir(), data_dir)
        pg.init(password)
        pg.start()
        dsn = pg.ensure_database(password)
        migrate_with_backup(pg, password, dsn, data_dir / "backups")
        db = Database(dsn)
        db.open()
    except Exception as exc:
        log.exception("startup failed")
        protocol.emit_error(ErrorCode.DB_UNAVAILABLE.value, str(exc))
        if pg is not None:
            pg.stop()
        return 2
    services = build_services(db, "all", store=store, data_dir=data_dir)
    app = create_app(handshake.token, db, services)
    app.state.data_dir = data_dir
    protocol.watch_eof(lambda: app.state.request_shutdown())
    services.start_all()
    try:
        serve(app, protocol.emit_ready)
    finally:
        services.stop_all()
        db.close()
        pg.stop()
    return 0


def _serve_dev(args: argparse.Namespace) -> int:
    logs.setup(os.environ.get("IR_LOG_LEVEL", "INFO"))
    token = _require_env("IR_DEV_TOKEN")
    dsn = _require_env("IR_DATABASE_URL")
    migrate(dsn)
    db = Database(dsn)
    db.open()
    data_dir = dev_data_dir()
    services = build_services(db, args.workers, data_dir=data_dir)
    app = create_app(token, db, services)
    app.state.data_dir = data_dir
    if args.watch_stdin:
        protocol.watch_eof(lambda: app.state.request_shutdown())
    services.start_all()
    try:
        serve(app, protocol.emit_ready, port=args.port)
    finally:
        services.stop_all()
        db.close()
    return 0


def cmd_db(args: argparse.Namespace) -> int:
    import psycopg

    if args.action == "migrate":
        applied = migrate(args.dsn)
        print(json.dumps({"applied": applied}))
    else:
        with psycopg.connect(args.dsn) as conn:
            print(json.dumps({"schema_version": current_version(conn)}))
    return 0


def cmd_fixtures(args: argparse.Namespace) -> int:
    from .fixtures import load

    migrate(args.dsn)
    stats = load(args.dsn, [Path(p) for p in args.paths], source_name=args.source_name, kind=args.kind)
    print(json.dumps(stats))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Back up or restore the embedded database while the app is closed (the running app holds the data directory)."""
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    password = shared_store().get(PG_PASSWORD)
    pg = EmbeddedPostgres(find_bin_dir(), data_dir)
    if password is None or not pg.initialized:
        return _report({"ok": False, "error": f"Không có database IR Desktop ở {data_dir}"}, 2)
    if args.action == "restore" and not args.file:
        return _report({"ok": False, "error": "--file is required"}, 2)
    try:
        pg.start()
    except PostgresError as exc:
        return _report({"ok": False, "error": f"{exc} (hãy đóng ứng dụng trước)"}, 2)
    try:
        if args.action == "create":
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out = pg.dump(password, data_dir / "backups" / f"irdesktop-manual-{stamp}.dump")
            return _report({"ok": True, "file": str(out)})
        pg.restore(password, Path(args.file))
        return _report({"ok": True, "restored": args.file})
    finally:
        pg.stop()


def _report(result: dict, code: int = 0) -> int:
    print(json.dumps(result, ensure_ascii=False))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ir-engine")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--dev", action="store_true")
    p_serve.add_argument("--port", type=int, default=0)
    p_serve.add_argument("--watch-stdin", action="store_true")
    p_serve.add_argument("--workers", choices=WORKER_CHOICES, default="all")
    p_serve.set_defaults(func=cmd_serve)

    p_db = sub.add_parser("db")
    p_db.add_argument("action", choices=["migrate", "status"])
    p_db.add_argument("--dsn", default=os.environ.get("IR_DATABASE_URL"), required=os.environ.get("IR_DATABASE_URL") is None)
    p_db.set_defaults(func=cmd_db)

    p_fx = sub.add_parser("fixtures")
    p_fx.add_argument("action", choices=["load"])
    p_fx.add_argument("paths", nargs="+")
    p_fx.add_argument("--source-name", default="sysmon")
    p_fx.add_argument("--kind", choices=["log", "alert"], default="log")
    p_fx.add_argument("--dsn", default=os.environ.get("IR_DATABASE_URL"), required=os.environ.get("IR_DATABASE_URL") is None)
    p_fx.set_defaults(func=cmd_fixtures)

    p_bk = sub.add_parser("backup")
    p_bk.add_argument("action", choices=["create", "restore"])
    p_bk.add_argument("--data-dir")
    p_bk.add_argument("--file")
    p_bk.set_defaults(func=cmd_backup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
