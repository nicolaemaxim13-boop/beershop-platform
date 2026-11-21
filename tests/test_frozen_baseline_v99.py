"""v9.9 frozen migration snapshot regression; synthetic DDL only."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'migrations_v95' / 'versions' / '0001_isolated_baseline.py'
SPEC = importlib.util.spec_from_file_location('beershop_v99_frozen_baseline', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_v99_baseline_sql_is_frozen_and_not_generated_from_current_models():
    sqlsource = PATH.read_text(encoding='utf-8')
    assert 'from app.main import Base' not in sqlsource
    assert 'Base.metadata.create_all' not in sqlsource
    assert 'exec_driver_sql(sql)' in sqlsource
    assert MODULE.revision == '0001_v95'
    assert MODULE.FROZEN_SHA256 == (
        '727f14082b385a6f6e0ac2bc2fc553b09a9876c097edd14c40e04cf03d768570')
    for dialect in ('sqlite', 'postgresql'):
        tables, sql = MODULE.load_frozen_baseline(dialect)
        assert len(tables) == 24
        assert len(set(tables)) == 24
        assert len(sql) == 46
        assert sum(x.startswith('CREATE TABLE ') for x in sql) == 24
        assert sum(x.startswith(('CREATE INDEX ', 'CREATE UNIQUE INDEX '))
                   for x in sql) == 22
        assert all(x.startswith(('CREATE TABLE ', 'CREATE INDEX ',
                                 'CREATE UNIQUE INDEX ')) for x in sql)
        assert not any('INSERT INTO ' in item or 'DROP ' in item
                       or 'TRUNCATE ' in item for item in sql)
    with pytest.raises(RuntimeError):
        MODULE.load_frozen_baseline('mysql')


def test_v99_tampered_frozen_sql_rejected_before_any_ddl(tmp_path,monkeypatch):
    manifest = json.loads(MODULE.FROZEN_PATH.read_text(encoding='utf-8'))
    manifest['dialects']['sqlite'][0] = (
        'CREATE TABLE bogus_changed_schema (id INTEGER)')
    path = tmp_path/'forged.json'
    path.write_text(json.dumps(manifest),encoding='utf-8')
    monkeypatch.setattr(MODULE,'FROZEN_PATH',path)
    with pytest.raises(RuntimeError, match='checksum'):
        MODULE.load_frozen_baseline('sqlite')
    path.write_text('{}',encoding='utf-8')
    with pytest.raises(RuntimeError):
        MODULE.load_frozen_baseline('postgresql')


def test_v99_frozen_snapshot_is_distinct_from_live_model_contract():
    from app.main import Base
    from app.schema_contract_v98 import ddl_contract
    live = ddl_contract(Base.metadata)
    tables,_ = MODULE.load_frozen_baseline('sqlite')
    assert set(tables) == {t.name for t in Base.metadata.sorted_tables} - {
        'v95_api_rate_counters'}
    assert live['table_count'] == 25
    assert live['historical_migration_frozen'] is False
