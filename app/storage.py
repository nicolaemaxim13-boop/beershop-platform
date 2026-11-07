"""One-time, fail-closed database copy from Render Postgres into a separate Neon project.

The source is read-only. A migration marker prevents replay on later restarts.
All copied tables and marker are committed in one destination transaction.
"""
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import create_engine, text, select, func, inspect

LOG = logging.getLogger('beershop.migration')

def sqlalchemy_url(url: str) -> str:
    if url.startswith('postgres://'):
        return 'postgresql+psycopg://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url

def migrate_once(metadata, origin_url: str, destination_engine):
    if not origin_url:
        raise RuntimeError('Source database URL missing; refusing migration')
    if sqlalchemy_url(origin_url) == str(destination_engine.url):
        raise RuntimeError('Source and destination are identical; refusing migration')
    metadata.create_all(destination_engine)
    ledger = text('CREATE TABLE IF NOT EXISTS _beershop_migration_info ('
                  'id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, counts TEXT NOT NULL)')
    tables = list(metadata.sorted_tables)
    with destination_engine.begin() as target:
        target.execute(ledger)
        stored = target.execute(text(
            'SELECT counts FROM _beershop_migration_info WHERE id=1')).scalar_one_or_none()
        if stored is not None:
            LOG.info('Persistent database already migrated; keeping all existing records')
            return json.loads(stored)
        existing = [t.name for t in tables
                    if target.scalar(select(func.count()).select_from(t))]
        if existing:
            raise RuntimeError('Target contains unmarked data; refusing overwrite: ' + ','.join(existing))
        origin_engine = create_engine(sqlalchemy_url(origin_url), pool_pre_ping=True)
        try:
            with origin_engine.connect() as source:
                counts = {}
                source_tables = set(inspect(source).get_table_names())
                for table in tables:
                    if table.name in {'crm_customers','crm_managers','crm_order_details','crm_notes',
                                      'stock_reservations','stock_movements'} and table.name not in source_tables:
                        counts[table.name] = 0
                        continue
                    rows = [dict(r._mapping) for r in source.execute(select(table)).all()]
                    if rows:
                        target.execute(table.insert(), rows)
                    a = source.scalar(select(func.count()).select_from(table))
                    b = target.scalar(select(func.count()).select_from(table))
                    if a != b or a != len(rows):
                        raise RuntimeError('Migration count mismatch for ' + table.name)
                    counts[table.name] = b
                if not counts.get('stores'):
                    raise RuntimeError('Source store missing; refusing migration')
                if destination_engine.dialect.name == 'postgresql':
                    for table in tables:
                        if 'id' not in table.c:
                            continue
                        quoted = destination_engine.dialect.identifier_preparer.quote(table.name)
                        seq = target.scalar(text(
                            'SELECT pg_get_serial_sequence(:table_name, :column_name)'),
                            {'table_name':table.name, 'column_name':'id'})
                        if seq:
                            target.execute(text(
                                'SELECT setval(CAST(:sequence AS regclass), '
                                'COALESCE(MAX(id),0)+1, false) FROM ' + quoted),
                                {'sequence':seq})
                target.execute(text(
                    'INSERT INTO _beershop_migration_info (id, created_at, counts) '
                    'VALUES (1, :when, :counts)'),
                    {'when':datetime.now(timezone.utc).isoformat(),
                     'counts':json.dumps(counts, sort_keys=True)})
                LOG.info('Migrated BeerShop tables to Neon: %s', counts)
                return counts
        finally:
            origin_engine.dispose()

