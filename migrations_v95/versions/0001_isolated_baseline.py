"""Immutable SQL baseline for disposable BeerShop fixture databases only.

Revision ID: 0001_v95
Revises:

This revision is intentionally restricted by migrations_v95/env.py.
The frozen DDL is versioned in frozen_0001_v99.json, authenticated by a
pinned source hash, and NEVER compiled from current app models at upgrade.
This does not authorize application of migrations to any live database.
"""
import hashlib
import json
import re
from pathlib import Path

from alembic import op
from sqlalchemy import inspect

revision = "0001_v95"
down_revision = None
branch_labels = None
depends_on = None

FROZEN_SHA256 = "727f14082b385a6f6e0ac2bc2fc553b09a9876c097edd14c40e04cf03d768570"
FROZEN_PATH = Path(__file__).resolve().parents[1] / "frozen_0001_v99.json"
BASELINE_TABLE_COUNT = 24


def load_frozen_baseline(dialect_name):
    if dialect_name not in ('postgresql', 'sqlite'):
        raise RuntimeError('Unsupported migration fixture dialect')
    try:
        manifest = json.loads(FROZEN_PATH.read_text(encoding='utf-8'))
        expected = manifest.pop('payload_sha256')
        canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=True,
                               separators=(',', ':')).encode('utf-8')
        actual = hashlib.sha256(canonical).hexdigest()
        if expected != FROZEN_SHA256 or actual != FROZEN_SHA256:
            raise RuntimeError('Frozen migration checksum mismatch')
        if (manifest.get('format') != 'beershop-v99-frozen-0001-DDL'
                or len(manifest.get('tables', [])) != BASELINE_TABLE_COUNT
                or len(set(manifest['tables'])) != BASELINE_TABLE_COUNT
                or set(manifest.get('dialects', {})) != {'sqlite', 'postgresql'}):
            raise RuntimeError('Frozen migration inventory invalid')
        sql = manifest['dialects'][dialect_name]
        if (not isinstance(sql, list)
                or sum(x.startswith('CREATE TABLE ') for x in sql) != BASELINE_TABLE_COUNT
                or not all(isinstance(x, str)
                           and re.fullmatch(r'CREATE (?:TABLE|(?:UNIQUE )?INDEX)\\b.*', x)
                           and ';' not in x and len(x) <= 10000 for x in sql)):
            raise RuntimeError('Frozen migration SQL not allowlisted')
        return manifest['tables'], sql
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError('Frozen migration snapshot unavailable or invalid') from None


def upgrade():
    conn = op.get_bind()
    existing = set(inspect(conn).get_table_names())
    if existing - {'alembic_version'}:
        raise RuntimeError('Baseline refuses nonempty database; never stamp live schemas')
    expected_tables, statements = load_frozen_baseline(conn.dialect.name)
    # All data-definition instructions are fixed in this revision's locked
    # JSON asset, never loaded from application metadata, database rows or env.
    for sql in statements:
        conn.exec_driver_sql(sql)
    actual_tables = set(inspect(conn).get_table_names())
    if actual_tables != set(expected_tables) | {'alembic_version'}:
        raise RuntimeError('Frozen migration table set verification failed')


def downgrade():
    raise RuntimeError('Downgrade disabled; use approved isolated backup recovery')
