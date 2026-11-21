"""v9.8 explicit DDL fingerprint lock for CI (no data or DB connections)."""
import json
from pathlib import Path

from sqlalchemy import MetaData, Table, Column, Integer

from app import main
from app.schema_contract_v98 import ddl_contract


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'migrations_v95' / 'schema_v98.lock.json'


def test_v98_schema_contract_matches_reviewed_lock():
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    live = ddl_contract(main.Base.metadata)
    assert lock['format'] == live['format']
    assert lock['table_count'] == live['table_count']
    assert lock['sha256'] == live['sha256']
    assert live['read_only']
    assert not live['historical_migration_frozen']
    assert live['manual_review_required']
    assert set(live['dialects']) == {'postgresql', 'sqlite'}
    assert live['table_count'] >= 25
    assert not any('INSERT INTO ' in ddl for group in live['sql'].values()
                   for ddl in group)


def test_v98_schema_contract_detects_unreviewed_schema_change():
    initial = ddl_contract(MetaData())
    changed = MetaData()
    Table('unapproved_change', changed, Column('id', Integer, primary_key=True))
    revised = ddl_contract(changed)
    assert initial['sha256'] != revised['sha256']
    assert initial['table_count'] == 0
    assert revised['table_count'] == 1
    # Contract contains only schema DDL and no customer rows.
    assert 'CREATE TABLE unapproved_change' in ' '.join(revised['sql']['sqlite'])
