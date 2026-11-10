"""BeerShop v9: integrity-checked backup and *offline*, isolated restore rehearsal.

This is not a production disaster recovery platform. Snapshots include buyer PII.
Never print their contents, upload them to public services or restore onto Neon.
"""
import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, func, select, text

SNAPSHOT_FORMAT = 'beershop-snapshot-v1'
INTEGRITY_FORMAT = 'beershop-integrity-v9'
MAX_ARCHIVE_SIZE = 25 * 1024 * 1024


class UnsafeBackup(ValueError):
    pass


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), default=str, allow_nan=False).encode('utf-8')


def _checksum(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _schema_id(metadata):
    tables = []
    for table in metadata.sorted_tables:
        tables.append({
            'name': table.name,
            'columns': [
                {'name': col.name, 'type': str(col.type),
                 'nullable': bool(col.nullable), 'primary_key': bool(col.primary_key)}
                for col in table.columns
            ],
        })
    return _checksum(tables)


def add_integrity(snapshot, metadata):
    """Adds a checksum to legacy v1 export without changing its data fields."""
    if snapshot.get('format') != SNAPSHOT_FORMAT:
        raise UnsafeBackup('Unsupported snapshot format')
    tables = snapshot.get('tables')
    if not isinstance(tables, dict):
        raise UnsafeBackup('Missing tables')
    # Normalize Decimal/date-like database values before hashing so the
    # in-memory manifest equals the downloaded UTF-8 JSON exactly.
    tables = json.loads(_canonical_bytes(tables))
    return {
        **snapshot,
        'tables': tables,
        'integrity': {
            'version': INTEGRITY_FORMAT,
            'algorithm': 'sha256',
            # SHA256 detects accidental corruption, NOT malicious replacement.
            'sha256': _checksum(tables),
            'schema_sha256': _schema_id(metadata),
            'counts': {name: len(rows) for name, rows in sorted(tables.items())},
        },
    }


def verify_snapshot(source, metadata):
    """Parse and structurally check an archive; never access a database."""
    if isinstance(source, bytes):
        if len(source) > MAX_ARCHIVE_SIZE:
            raise UnsafeBackup('Snapshot too large')
        try:
            source = source.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise UnsafeBackup('Snapshot is not UTF-8') from exc
    if isinstance(source, str):
        if len(source.encode('utf-8')) > MAX_ARCHIVE_SIZE:
            raise UnsafeBackup('Snapshot too large')
        try:
            source = json.loads(source)
        except (json.JSONDecodeError, ValueError) as exc:
            raise UnsafeBackup('Invalid JSON') from exc
    if not isinstance(source, dict) or source.get('format') != SNAPSHOT_FORMAT:
        raise UnsafeBackup('Unsupported or missing snapshot format')
    try:
        timestamp = datetime.fromisoformat(source['created_at'])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsafeBackup('Invalid snapshot timestamp') from exc
    if timestamp.tzinfo is None:
        raise UnsafeBackup('Snapshot timestamp must have timezone')

    tables = source.get('tables')
    if not isinstance(tables, dict):
        raise UnsafeBackup('Snapshot tables missing')
    expected = {table.name: table for table in metadata.sorted_tables}
    if set(tables) != set(expected):
        raise UnsafeBackup('Snapshot schema tables differ from application')
    integrity = source.get('integrity')
    if not isinstance(integrity, dict) or integrity.get('version') != INTEGRITY_FORMAT:
        raise UnsafeBackup('Missing v9 integrity manifest; legacy backup is not restore-ready')
    if integrity.get('algorithm') != 'sha256':
        raise UnsafeBackup('Unknown checksum algorithm')
    if not isinstance(integrity.get('sha256'), str) or len(integrity['sha256']) != 64:
        raise UnsafeBackup('Invalid checksum')
    if not isinstance(integrity.get('schema_sha256'), str):
        raise UnsafeBackup('Invalid schema checksum')
    if integrity['schema_sha256'] != _schema_id(metadata):
        raise UnsafeBackup('Archive was built for a different schema')

    counts = {}
    for name, table in expected.items():
        rows = tables[name]
        if not isinstance(rows, list):
            raise UnsafeBackup('Invalid rows for table ' + name)
        allowed = {col.name for col in table.columns}
        required = {c.name for c in table.primary_key.columns}
        for row in rows:
            if (not isinstance(row, dict) or
                    not required.issubset(row) or not set(row).issubset(allowed) or
                    not all(v is None or type(v) in (str, int, float, bool) for v in row.values())):
                raise UnsafeBackup('Invalid row structure for table ' + name)
        counts[name] = len(rows)

    if integrity.get('counts') != dict(sorted(counts.items())):
        raise UnsafeBackup('Snapshot row counts do not match')
    digest = _checksum(tables)
    if not secrets.compare_digest(digest, integrity['sha256']):
        raise UnsafeBackup('Snapshot SHA256 mismatch')
    if not any(r.get('id') == 1 for r in tables['stores']):
        raise UnsafeBackup('Store 1 missing from snapshot')

    return {'ok': True, 'format': INTEGRITY_FORMAT,
            'schema_sha256': integrity['schema_sha256'],
            'sha256': digest, 'counts': dict(sorted(counts.items())),
            'total_rows': sum(counts.values())}


def rehearsal_restore_new_sqlite(source, metadata, target_path):
    """Restore into an absent, local temp SQLite file only. Never overwrite DBs."""
    report = verify_snapshot(source, metadata)
    if not isinstance(source, dict):
        source = json.loads(source)
    target = Path(target_path).absolute()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (target.suffix != '.db' or target.is_symlink() or
            temp_root not in target.parents or not target.parent.is_dir() or
            target.exists()):
        raise UnsafeBackup('Restore requires a NEW .db file under the local temp directory')

    # Exclusive creation ensures an existing database is not accidentally replaced.
    with target.open('xb'):
        pass
    engine = None
    try:
        engine = create_engine('sqlite:///' + str(target))
        @event.listens_for(engine, 'connect')
        def enable_fk(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()

        metadata.create_all(engine)
        with engine.begin() as connection:
            for table in metadata.sorted_tables:
                rows = source['tables'][table.name]
                if rows:
                    connection.execute(table.insert(), rows)
                actual = connection.scalar(select(func.count()).select_from(table))
                if actual != report['counts'][table.name]:
                    raise UnsafeBackup('Restore count mismatch for table ' + table.name)
            violated = connection.execute(text('PRAGMA foreign_key_check')).first()
            if violated:
                raise UnsafeBackup('Restore foreign key validation failed')
        return {'ok': True, 'mode': 'LOCAL_REHEARSAL_ONLY',
                'checks': report, 'target': str(target)}
    except Exception:
        if engine is not None:
            engine.dispose()
        target.unlink(missing_ok=True)
        raise
    finally:
        if engine is not None:
            engine.dispose()
