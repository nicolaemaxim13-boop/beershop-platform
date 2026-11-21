"""v9.8 schema-managed startup is read-only and refuses unversioned fixtures."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, select, func
from sqlalchemy.orm import sessionmaker

import app.main as main
from app.startup_preflight_v98 import (
    ACK, EXPECTED_REVISION, verify_premigrated_synthetic_schema)


def configure(monkeypatch):
    monkeypatch.setenv('BEERSHOP_V98_SCHEMA_MANAGED','1')
    monkeypatch.setenv('BEERSHOP_V98_SCHEMA_ACK', ACK)
    monkeypatch.setenv('BEERSHOP_V91_STAGING_ACK','ISOLATED_STAGING_ONLY')
    monkeypatch.setenv('BEERSHOP_V91_ENFORCE_RBAC','1')
    monkeypatch.setenv('BEERSHOP_V9_STAFF_AUTH','1')
    monkeypatch.delenv('BEERSHOP_V98_ENCRYPTED_EXPORT', raising=False)
    monkeypatch.delenv('BEERSHOP_V95_RATE_LIMIT', raising=False)
    monkeypatch.delenv('BEERSHOP_V96_EDGE_SIGNED_CLIENT', raising=False)


def new_fixture(tmp_path, *, version=None, create_schema=True):
    import uuid
    folder = tmp_path / uuid.uuid4().hex[:8]
    folder.mkdir()
    path = folder / 'beershop_ci_test.db'
    engine = create_engine('sqlite:///' + str(path))
    if create_schema:
        main.Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(main.Store.__table__.insert(), {
                'id':1,'slug':'synthetic','name':'V98_OLDER_STORE_KEEP_AS_IS'})
    if version is not None:
        with engine.begin() as connection:
            connection.execute(text(
                'CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
            connection.execute(text('INSERT INTO alembic_version VALUES (:v)'),
                               {'v':version})
    return engine


def test_v98_readonly_schema_gate_accepts_only_premigrated_fixture(tmp_path,monkeypatch):
    configure(monkeypatch)
    engine = new_fixture(tmp_path, version=EXPECTED_REVISION)
    try:
        with engine.connect() as c:
            before = c.execute(text(
                "SELECT id, slug, name FROM stores ORDER BY id")).all()
        result = verify_premigrated_synthetic_schema(
            engine=engine, metadata=main.Base.metadata,
            database_url=str(engine.url), persistent_url='')
        assert result == {
            'read_only': True, 'schema_matches': True,
            'alembic_revision': EXPECTED_REVISION,
            'auto_bootstrap_disabled': True,
        }
        with engine.connect() as c:
            after = c.execute(text(
                "SELECT id, slug, name FROM stores ORDER BY id")).all()
            assert before == after
    finally:
        engine.dispose()


def test_v98_schema_guard_blocks_missing_revision_and_drift(tmp_path,monkeypatch):
    configure(monkeypatch)
    engine = new_fixture(tmp_path)
    try:
        with pytest.raises(HTTPException) as err:
            verify_premigrated_synthetic_schema(
                engine=engine, metadata=main.Base.metadata,
                database_url=str(engine.url), persistent_url='')
        assert err.value.status_code == 503
        with engine.begin() as c:
            c.execute(text(
                'CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
            c.execute(text("INSERT INTO alembic_version VALUES ('0001_v95')"))
        with pytest.raises(HTTPException):
            verify_premigrated_synthetic_schema(
                engine=engine, metadata=main.Base.metadata,
                database_url=str(engine.url), persistent_url='')
        with engine.begin() as c:
            c.execute(text("UPDATE alembic_version SET version_num='0002_v95'"))
            c.execute(text("CREATE TABLE unexpected_customers (id INTEGER)"))
        with pytest.raises(HTTPException):
            verify_premigrated_synthetic_schema(
                engine=engine, metadata=main.Base.metadata,
                database_url=str(engine.url), persistent_url='')
        with engine.connect() as c:
            assert c.execute(text("SELECT name FROM stores")).scalar_one() == (
                'V98_OLDER_STORE_KEEP_AS_IS')
    finally:
        engine.dispose()


def test_v98_schema_managed_lifespan_skips_ddl_and_backfill(tmp_path,monkeypatch):
    configure(monkeypatch)
    engine = new_fixture(tmp_path, version=EXPECTED_REVISION)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(main, 'DB_URL', str(engine.url))
    monkeypatch.setattr(main, 'PERSISTENT_URL', '')
    monkeypatch.setattr(main, 'SessionLocal',
                        sessionmaker(engine,expire_on_commit=False))
    try:
        with TestClient(main.app) as client:
            response=client.get('/api/categories')
            assert response.status_code == 200, response.text
            # Normal legacy startup creates four default categories.
            assert response.json() == []
        with engine.connect() as db:
            assert db.scalar(select(func.count()).select_from(main.Category)) == 0
            assert db.scalar(select(main.Store.name)) == 'V98_OLDER_STORE_KEEP_AS_IS'
    finally:
        engine.dispose()


def test_v98_schema_managed_startup_refuses_unversioned_without_creating(tmp_path,monkeypatch):
    configure(monkeypatch)
    engine = new_fixture(tmp_path,create_schema=False)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(main, 'DB_URL', str(engine.url))
    monkeypatch.setattr(main, 'PERSISTENT_URL','')
    monkeypatch.setattr(main, 'SessionLocal',
                        sessionmaker(engine,expire_on_commit=False))
    try:
        with pytest.raises(HTTPException) as exc:
            with TestClient(main.app):
                pass
        assert exc.value.status_code == 503
        with engine.connect() as c:
            from sqlalchemy import inspect
            assert inspect(c).get_table_names() == []  # NO CREATE_ALL
    finally:
        engine.dispose()


def test_v98_schema_guard_refuses_remote_live_and_mismatched_engine(tmp_path,monkeypatch):
    configure(monkeypatch)
    engine = new_fixture(tmp_path,version=EXPECTED_REVISION)
    try:
        for url,persistent in (
            ('postgresql://fake:fake@ep-neon.neon.tech/beershop_test',''),
            ('sqlite:///./real-customer.db',''),
            (str(engine.url),'postgresql://fake:fake@neon.invalid/live'),
        ):
            with pytest.raises(HTTPException):
                verify_premigrated_synthetic_schema(
                    engine=engine, metadata=main.Base.metadata,
                    database_url=url,persistent_url=persistent)
        monkeypatch.delenv('BEERSHOP_V98_SCHEMA_ACK')
        with pytest.raises(HTTPException):
            verify_premigrated_synthetic_schema(
                engine=engine, metadata=main.Base.metadata,
                database_url=str(engine.url),persistent_url='')
    finally:
        engine.dispose()
