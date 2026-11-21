"""v9.4 test-only PostgreSQL restore into an explicitly named EMPTY local database.

No production credentials or Neon URLs. This helper never drops or overwrites
tables, does not migrate existing schemas and leaves the test database intact
for external inspection. CI disposes of its ephemeral Postgres container.
"""
import os

from sqlalchemy import create_engine, inspect, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .backup_v9 import verify_snapshot, UnsafeBackup

REHEARSAL_DB_NAME = 'beershop_v94_rehearsal'
ENCRYPTED_REHEARSAL_DB_NAME = 'beershop_v96_encrypted_restore'


def validate_local_rehearsal_target(url: str, *, expected_name=REHEARSAL_DB_NAME):
    if (os.getenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL') != '1'
            or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL')):
        raise UnsafeBackup('Local PostgreSQL rehearsal is not enabled')
    try:
        parsed = make_url(url)
        backend = parsed.get_backend_name()
    except (ArgumentError, TypeError, ValueError):
        raise UnsafeBackup('Invalid database URL') from None
    if (backend != 'postgresql'
            or parsed.host not in ('localhost', '127.0.0.1', '::1')
            or expected_name not in (REHEARSAL_DB_NAME, ENCRYPTED_REHEARSAL_DB_NAME)
            or parsed.database != expected_name):
        raise UnsafeBackup('Rehearsal requires named local test-only PostgreSQL')
    return parsed


def rehearsal_restore_empty_postgres(source, metadata, url, *, expected_name=REHEARSAL_DB_NAME):
    """Verify snapshot, enforce test-only target, then restore atomically.

    Never drop, truncate, update or overwrite a previously existing table.
    PostgreSQL DDL and all inserts occur in one transaction.
    """
    report = verify_snapshot(source, metadata)
    validate_local_rehearsal_target(url, expected_name=expected_name)
    from .storage import sqlalchemy_url
    engine = create_engine(sqlalchemy_url(url), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            # Schema inspection happens inside transaction; this utility
            # is not safe as a concurrent multi-user production tool.
            existing = inspect(connection).get_table_names(schema='public')
            if existing:
                raise UnsafeBackup('Rehearsal target must have no tables')
            metadata.create_all(connection)
            for table in metadata.sorted_tables:
                rows = source['tables'][table.name] if isinstance(source, dict) else None
                if rows is None:
                    import json
                    rows = json.loads(source)['tables'][table.name]
                if rows:
                    connection.execute(table.insert(), rows)
                count = int(connection.scalar(select(func.count()).select_from(table)))
                if count != report['counts'][table.name]:
                    raise UnsafeBackup('PostgreSQL restore row-count mismatch')
                # Allow fresh subsequent synthetic inserts in the restored DB.
                if 'id' in table.c:
                    sequence = connection.scalar(
                        __import__('sqlalchemy').text(
                            'SELECT pg_get_serial_sequence(:table_name, :column_name)'),
                        {'table_name': table.name, 'column_name': 'id'})
                    if sequence:
                        # Sequence name is returned by PostgreSQL, not user input.
                        connection.execute(
                            __import__('sqlalchemy').text(
                                'SELECT setval(CAST(:seq AS regclass), '
                                'COALESCE((SELECT MAX(id) FROM "' + table.name + '"),0)+1, false)'),
                            {'seq': sequence})
        return {'ok': True, 'mode': 'ISOLATED_PG_REHEARSAL_ONLY',
                'tables': len(report['counts']),
                'rows': report['total_rows'], 'sha256': report['sha256']}
    finally:
        engine.dispose()
