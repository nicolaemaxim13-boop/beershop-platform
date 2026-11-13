"""v9.5 Alembic upgrade is allowed only on disposable synthetic fixtures."""
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text

from app import main
from app.schema_audit_v94 import schema_inventory
from app.storage import sqlalchemy_url

ROOT = Path(__file__).resolve().parents[1]


def alembic_run(db_url, *args, postgres=False, ack=True):
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.pop("BEERSHOP_PERSISTENT_DATABASE_URL", None)
    env["BEERSHOP_V95_MIGRATION_URL"] = db_url
    env["BEERSHOP_V95_MIGRATION_ACK"] = (
        "ISOLATED_FIXTURE_ONLY" if ack else "not-confirmed")
    if postgres:
        env["BEERSHOP_V95_ALLOW_LOCAL_PG_MIGRATION"] = "1"
    else:
        env.pop("BEERSHOP_V95_ALLOW_LOCAL_PG_MIGRATION", None)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic_v95.ini", *args],
        cwd=ROOT, env=env, text=True, capture_output=True,
        timeout=90, check=False)


def test_v95_sqlite_migrations_clean_baseline_and_repeat(tmp_path):
    name = tmp_path / ("beershop-v95-migration-" + uuid4().hex[:8] + ".db")
    url = "sqlite:///" + str(name)
    installed = alembic_run(url, "upgrade", "head")
    assert installed.returncode == 0, installed.stderr
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT version_num FROM alembic_version")) == "0002_v95"
            assert schema_inventory(connection, main.Base.metadata)[
                "schema_matches_columns"] is True
            assert "v95_api_rate_counters" in inspect(connection).get_table_names()
        repeated = alembic_run(url, "upgrade", "head")
        assert repeated.returncode == 0, repeated.stderr
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT COUNT(*) FROM alembic_version")) == 1
    finally:
        engine.dispose()


def test_v95_migrations_refuse_nonempty_and_remote_db(tmp_path):
    name = tmp_path / ("beershop-v95-migration-" + uuid4().hex[:8] + ".db")
    engine = create_engine("sqlite:///" + str(name))
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_customer_data (id INTEGER)"))
        connection.execute(text("INSERT INTO existing_customer_data VALUES (101)"))
    engine.dispose()
    rejected = alembic_run("sqlite:///" + str(name), "upgrade", "head")
    assert rejected.returncode != 0
    engine = create_engine("sqlite:///" + str(name))
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT id FROM existing_customer_data")) == 101
    engine.dispose()
    refused = alembic_run("postgresql://no:fake@ep-neon.neon.tech/live",
                          "upgrade", "head")
    assert refused.returncode != 0
    no_ack = alembic_run("sqlite:///" + str(tmp_path /
            ("beershop-v95-migration-" + uuid4().hex[:8] + ".db")),
            "upgrade", "head", ack=False)
    assert no_ack.returncode != 0
    bad_name = alembic_run("sqlite:///" + str(tmp_path / "customer.db"),
                           "upgrade", "head")
    assert bad_name.returncode != 0


def test_v95_pg_alembic_only_in_disposable_ci_database():
    url = os.getenv("BEERSHOP_V95_PG_MIGRATION_URL")
    if not url:
        pytest.skip("Separate isolated PG migration fixture is available in CI only")
    run = alembic_run(url, "upgrade", "head", postgres=True)
    assert run.returncode == 0, run.stderr
    engine = create_engine(sqlalchemy_url(url))
    try:
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT version_num FROM alembic_version")) == "0002_v95"
            assert schema_inventory(connection, main.Base.metadata)[
                "schema_matches_columns"] is True
    finally:
        engine.dispose()
