import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import logs, protocol
from .app import create_app
from .contracts import ErrorCode
from .credentials import PG_PASSWORD, default_store, get_or_create
from .db import Database
from .db.migrate import current_version, migrate
from .runtime.postgres import EmbeddedPostgres, find_bin_dir
from .runtime.workers import WORKER_CHOICES, build_services
from .server import serve

log = logging.getLogger("ir_engine")


def default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "IRDesktop"
    return Path.home() / ".local" / "share" / "IRDesktop"


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
        store = default_store()
        password = get_or_create(store, PG_PASSWORD)
        pg = EmbeddedPostgres(find_bin_dir(), data_dir)
        pg.init(password)
        pg.start()
        dsn = pg.ensure_database(password)
        migrate(dsn)
        db = Database(dsn)
        db.open()
    except Exception as exc:
        log.exception("startup failed")
        protocol.emit_error(ErrorCode.DB_UNAVAILABLE.value, str(exc))
        if pg is not None:
            pg.stop()
        return 2
    services = build_services(db, "all")
    app = create_app(handshake.token, db, services)
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
    services = build_services(db, args.workers)
    app = create_app(token, db, services)
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
