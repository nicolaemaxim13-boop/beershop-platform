"""v9.6 read-only schema preflight for already populated synthetic fixtures."""
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text, select

from app import main
from app.migration_preflight_v96 import (
    UnsafeMigrationPreflight, inspect_isolated_existing, inspect_existing)
from app.storage import sqlalchemy_url

ROOT = Path(__file__).resolve().parents[1]


def enable(monkeypatch):
    monkeypatch.setenv('BEERSHOP_V96_PREFLIGHT_ACK', 'ISOLATED_READONLY_PREFLIGHT')
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)


def test_existing_sqlite_readonly_fixture_and_drift(tmp_path, monkeypatch):
    enable(monkeypatch)
    database = tmp_path / ('beershop-v96-preflight-' + uuid4().hex[:8] + '.db')
    url = 'sqlite:///' + str(database)
    engine = create_engine(url)
    main.Base.metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(main.Store.__table__.insert(), {
            'id':1, 'slug':'synthetic', 'name':'PRIVATE-SYNTHETIC-NAME'})
    engine.dispose()
    database.chmod(0o600)

    report = inspect_isolated_existing(url, main.Base.metadata)
    assert report['read_only'] and report['requires_manual_baseline_review']
    assert report['existing_schema_matches_model'], report['issues']
    assert not report['alembic_version_present']
    assert 'PRIVATE-SYNTHETIC-NAME' not in str(report)

    # The auditor must not change existing schema/data in any circumstances.
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text('CREATE TABLE additional_unknown_table (id INTEGER)'))
    engine.dispose()
    drift = inspect_isolated_existing(url, main.Base.metadata)
    assert not drift['existing_schema_matches_model']
    assert drift['issues']['extra_tables'] == ['additional_unknown_table']
    engine = create_engine(url)
    with engine.connect() as db:
        assert db.scalar(select(main.Store.name)) == 'PRIVATE-SYNTHETIC-NAME'
    engine.dispose()

    env = os.environ.copy()
    env.pop('DATABASE_URL', None)
    env.pop('BEERSHOP_PERSISTENT_DATABASE_URL', None)
    env['BEERSHOP_V96_PREFLIGHT_ACK'] = 'ISOLATED_READONLY_PREFLIGHT'
    env['BEERSHOP_V96_PREFLIGHT_URL'] = url
    result = subprocess.run([sys.executable, 'scripts/migration_preflight_v96.py'],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=60, check=False)
    assert result.returncode == 3
    assert 'additional_unknown_table' in result.stdout
    assert 'PRIVATE-SYNTHETIC-NAME' not in result.stdout + result.stderr
    assert str(database) not in result.stdout + result.stderr


def test_existing_schema_preflight_refuses_remote_and_nonfixture(monkeypatch, tmp_path):
    enable(monkeypatch)
    for url in (
        'postgresql://fake:fake@ep-neon.neon.tech/beershop_v96_preflight',
        'postgresql://fake:fake@localhost/beershop_test',
        'sqlite:///./relative.db',
        'sqlite:///' + str(tmp_path/'customers.db'),
    ):
        with pytest.raises(UnsafeMigrationPreflight):
            inspect_isolated_existing(url, main.Base.metadata)
    monkeypatch.setenv('DATABASE_URL','postgresql://not:real@localhost/live')
    with pytest.raises(UnsafeMigrationPreflight):
        inspect_isolated_existing(
            'sqlite:///' + str(tmp_path/'beershop-v96-preflight-aaa.db'),
            main.Base.metadata)


def test_postgresql_existing_read_only_preflight_in_disposable_ci(monkeypatch):
    url = os.getenv('BEERSHOP_V96_PG_PREFLIGHT_URL')
    if not url:
        pytest.skip('Only PostgreSQL CI creates the disposable schema preflight DB')
    enable(monkeypatch)
    monkeypatch.setenv('BEERSHOP_V96_LOCAL_PG_PREFLIGHT', '1')
    engine = create_engine(sqlalchemy_url(url))
    try:
        main.Base.metadata.create_all(engine)
        with engine.begin() as db:
            db.execute(main.Store.__table__.insert(), {
                'id':1, 'slug':'staging', 'name':'PG-SYNTHETIC-DO-NOT-USE'})
        result = inspect_isolated_existing(url, main.Base.metadata)
        assert result['read_only']
        assert result['existing_schema_matches_model'], result['issues']
        with engine.connect() as db:
            assert db.scalar(select(main.Store.name)) == 'PG-SYNTHETIC-DO-NOT-USE'
    finally:
        engine.dispose()
