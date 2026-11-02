import os
import uuid
from pathlib import Path

import psycopg
import pytest

from ir_engine.db.migrate import migrate
from ir_engine.runtime.postgres import EmbeddedPostgres, find_bin_dir

REPO = Path(__file__).resolve().parents[2]
CONTRACTS = REPO / "contracts"
FIXTURES = REPO / "fixtures" / "sysmon"
PASSWORD = "test-password-" + uuid.uuid4().hex


@pytest.fixture(scope="session")
def pg(tmp_path_factory):
    server = EmbeddedPostgres(find_bin_dir(), tmp_path_factory.mktemp("pgroot"))
    server.init(PASSWORD)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def template_db(pg):
    dsn = pg.ensure_database(PASSWORD)
    migrate(dsn)
    return "irdesktop"


@pytest.fixture
def dsn(pg, template_db):
    name = "t_" + uuid.uuid4().hex[:12]
    admin = pg.dsn(PASSWORD, "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"')
    yield pg.dsn(PASSWORD, name)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def engine_env(dsn):
    env = dict(os.environ)
    env.update({
        "IR_DEV_TOKEN": "t" * 64,
        "IR_DATABASE_URL": dsn,
        "IR_SECRET_BACKEND": "memory",
        "PYTHONPATH": str(REPO / "engine"),
    })
    return env
