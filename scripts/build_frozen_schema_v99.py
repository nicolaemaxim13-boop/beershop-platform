#!/usr/bin/env python3
"""Build *review-only* frozen 0001 SQL from in-memory SQLAlchemy metadata.

The generated output is **never** applied to any database by this script.
Only deterministic CREATE TABLE/INDEX statements, no data or credentials.
The schema has 24 baseline tables (v95 rate counter belongs to 0002).
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.dialects import postgresql, sqlite
from app.main import Base

def compact(sql):
    return re.sub(r'\s+', ' ', str(sql)).strip()

def build():
    tables = [t for t in Base.metadata.sorted_tables
              if t.name != 'v95_api_rate_counters']
    if len(tables) != 24:
        raise RuntimeError('Baseline inventory changed; manual review needed')
    output = {'format': 'beershop-v99-frozen-0001-DDL',
              'source': 'synthetic-staging-SQLAlchemy-2026-11',
              'tables': [t.name for t in tables],
              'dialects': {}}
    for name, dialect in (('sqlite', sqlite.dialect()),
                          ('postgresql', postgresql.dialect())):
        statements = []
        for table in tables:
            statements.append(compact(CreateTable(table).compile(dialect=dialect)))
            for index in sorted(table.indexes, key=lambda i: i.name or ''):
                statements.append(compact(CreateIndex(index).compile(dialect=dialect)))
        if not all(re.match(r'^CREATE (?:TABLE|(?:UNIQUE )?INDEX)\b', sql)
                   and ';' not in sql for sql in statements):
            raise RuntimeError('Generated DDL outside allowlisted CREATE statements')
        output['dialects'][name] = statements
    canonical = json.dumps(output, sort_keys=True, ensure_ascii=True,
                           separators=(',', ':')).encode('utf-8')
    output['payload_sha256'] = hashlib.sha256(canonical).hexdigest()
    return output

if __name__ == '__main__':
    result = build()
    # DDL contains only schema definitions and no rows. The sentinel allows
    # the tool-driven review to capture a single self-contained line.
    print('BEERSHOP_V99_FROZEN_MANIFEST=' +
          json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(',', ':')))
