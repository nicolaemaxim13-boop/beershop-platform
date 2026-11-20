"""v10.0 read-only adoption planning against isolated test-only SQLite."""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select, text

from app import main
from app.adoption_plan_v100 import (
    EXPECTED_REVISION, inspect_isolated_existing_plan,
    _explicit_index_signature,
)
from app.migration_preflight_v96 import UnsafeMigrationPreflight


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv('BEERSHOP_V96_PREFLIGHT_ACK', 'ISOLATED_READONLY_PREFLIGHT')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    database = tmp_path / ('beershop-v96-preflight-' + uuid4().hex[:8] + '.db')
    url = 'sqlite:///' + str(database)
    engine = create_engine(url)
    main.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(main.Store.__table__.insert(), {
            'id': 1, 'slug': 'fake', 'name': 'V100_FAKE_BUYER_PRIVATE_VALUE',
        })
    engine.dispose()
    database.chmod(0o600)
    return url, database


def test_v100_unversioned_matching_existing_schema_is_review_only(tmp_path, monkeypatch):
    url, database = _setup(tmp_path, monkeypatch)
    result = inspect_isolated_existing_plan(url, main.Base.metadata)
    assert result['decision'] == 'NO_GO'
    assert result['read_only'] is True
    assert result['revision_status'] == 'UNVERSIONED'
    assert result['candidate_for_manual_review'] is True, result['index_issues']
    assert result['index_inventory_matches_models'] is True
    assert result['writes_to_database'] == 0
    assert result['sql_migrations_run'] == 0
    assert result['rows_exposed'] == 0
    assert str(database) not in str(result)
    assert 'V100_FAKE_BUYER_PRIVATE_VALUE' not in str(result)
    with create_engine(url).connect() as db:
        assert db.scalar(select(main.Store.name)) == 'V100_FAKE_BUYER_PRIVATE_VALUE'
        assert 'alembic_version' not in inspect(db).get_table_names()


def test_v100_expected_and_ambiguous_revisions_never_approve(tmp_path, monkeypatch):
    url, _ = _setup(tmp_path, monkeypatch)
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text('CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL)'))
        db.execute(text('INSERT INTO alembic_version VALUES (:v)'), {'v': EXPECTED_REVISION})
    report = inspect_isolated_existing_plan(url, main.Base.metadata)
    assert report['revision_status'] == 'EXPECTED_STAGING_REVISION'
    assert report['decision'] == 'NO_GO'
    with engine.begin() as db:
        db.execute(text('INSERT INTO alembic_version VALUES (:v)'), {'v': '0001_v95'})
    report = inspect_isolated_existing_plan(url, main.Base.metadata)
    assert report['revision_status'] == 'UNRECOGNIZED_OR_AMBIGUOUS'
    assert report['candidate_for_manual_review'] is False
    engine.dispose()


def test_v100_index_schema_drift_detected_without_mutating(tmp_path, monkeypatch):
    url, _ = _setup(tmp_path, monkeypatch)
    engine = create_engine(url)
    table = next(t for t in main.Base.metadata.sorted_tables if t.indexes)
    index_name = sorted(i.name for i in table.indexes)[0]
    with engine.begin() as db:
        db.execute(text('DROP INDEX "' + index_name.replace('"', '""') + '"'))
        db.execute(text('CREATE TABLE fake_unexpected_table (id INTEGER)'))
    report = inspect_isolated_existing_plan(url, main.Base.metadata)
    assert report['decision'] == 'NO_GO'
    assert report['schema_matches_models'] is False
    assert report['index_inventory_matches_models'] is False
    assert index_name in report['index_issues']['missing_indexes'][table.name]
    assert 'fake_unexpected_table' in report['schema_issues']['extra_tables']
    assert report['candidate_for_manual_review'] is False
    engine.dispose()


def test_v100_unknown_partial_or_expression_indexes_are_conservative():
    assert _explicit_index_signature({'name': 'x', 'column_names': [None]}) is None
    assert _explicit_index_signature({'name': 'x', 'column_names': ['id'],
                                      'dialect_options': {'postgresql_where': 'foo'}}) is None
    assert _explicit_index_signature({'name': 'x', 'column_names': ['id'],
                                      'duplicates_constraint': 'uq_x'}) == 'skip'


def test_v100_remote_and_nonfixture_targets_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv('BEERSHOP_V96_PREFLIGHT_ACK', 'ISOLATED_READONLY_PREFLIGHT')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    for url in (
        'postgresql://fake:fake@remote.neon.tech/beershop_v96_preflight',
        'sqlite:///./real-customers.db',
        'sqlite:///' + str(tmp_path/'real-customers.db'),
    ):
        with pytest.raises(UnsafeMigrationPreflight):
            inspect_isolated_existing_plan(url, main.Base.metadata)
    url, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fake:fake@remote.invalid/live')
    with pytest.raises(UnsafeMigrationPreflight):
        inspect_isolated_existing_plan(url, main.Base.metadata)


def test_v100_cli_is_readonly_no_go_and_never_prints_db_url(tmp_path, monkeypatch):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    url, database = _setup(tmp_path, monkeypatch)
    env = dict(os.environ)
    env['BEERSHOP_V96_PREFLIGHT_ACK'] = 'ISOLATED_READONLY_PREFLIGHT'
    env['BEERSHOP_V96_PREFLIGHT_URL'] = url
    env.pop('DATABASE_URL', None)
    env.pop('BEERSHOP_PERSISTENT_DATABASE_URL', None)
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, 'scripts/adoption_plan_v100.py']
    run = subprocess.run(cmd, cwd=root, env=env, capture_output=True,
                         text=True, timeout=60, check=False)
    assert run.returncode == 3, run.stderr
    result = json.loads(run.stdout)
    assert result['decision'] == 'NO_GO'
    assert result['rows_exposed'] == 0
    assert 'V100_FAKE_BUYER_PRIVATE_VALUE' not in run.stdout + run.stderr
    assert str(database) not in run.stdout + run.stderr
    assert url not in run.stdout + run.stderr

    env['DATABASE_URL'] = 'postgresql://private:secret@remote.invalid/live'
    denied = subprocess.run(cmd, cwd=root, env=env, capture_output=True,
                            text=True, timeout=60, check=False)
    assert denied.returncode == 2
    assert denied.stdout == ''
    assert 'secret' not in denied.stderr
