"""Read-only, deterministic v9.8 DDL contract for migration planning.

Generates dialect-specific DDL from current SQLAlchemy metadata in memory.
No database connections, no data, no credentials. The contract is a frozen
MODEL signature, not a historical executable Alembic migration.
"""
import hashlib
import json
import re

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable


def _one_line(sql):
    return re.sub(r'\s+', ' ', str(sql)).strip()


def ddl_contract(metadata):
    compiled = {}
    tables = sorted(metadata.sorted_tables, key=lambda t: t.name)
    for name, dialect in (
        ('postgresql', postgresql.dialect()),
        ('sqlite', sqlite.dialect()),
    ):
        statements = []
        for table in tables:
            statements.append('TABLE ' + table.name + ' ' +
                              _one_line(CreateTable(table).compile(dialect=dialect)))
            for index in sorted(table.indexes, key=lambda x: x.name or ''):
                statements.append('INDEX ' + str(index.name) + ' ' +
                                  _one_line(CreateIndex(index).compile(dialect=dialect)))
        compiled[name] = statements
    payload = json.dumps(compiled, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return {
        'format': 'beershop-v98-schema-model-ddl-contract',
        'read_only': True,
        'table_count': len(tables),
        'dialects': sorted(compiled),
        'sha256': hashlib.sha256(payload).hexdigest(),
        'sql': compiled,
        'historical_migration_frozen': False,
        'manual_review_required': True,
    }
