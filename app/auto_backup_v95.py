"""v9.5 automated, encryption-first backup runner for SYNTHETIC local SQLite only.

No network database, no SQL export plaintext files, no real customer accounts.
Operators can schedule run_once with a local timer/cron; run_forever is a
single-host isolated staging job and is NOT running automatically on Render.
"""
import fcntl
import json
import os
import re
import secrets
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from .backup_v9 import add_integrity, verify_snapshot
from .schema_audit_v94 import schema_inventory
from .secure_backup_v94 import encrypt_archive, decrypt_archive, write_private_exclusive

ARCHIVE_NAME = re.compile(r'^v95-\d{8}T\d{6}Z-[a-f0-9]{16}\.bse$')


class UnsafeScheduledBackup(ValueError):
    pass


def validate_synthetic_paths(source, destination, secret_file):
    if (os.getenv('BEERSHOP_V95_SYNTHETIC_BACKUP_ACK')
            != 'I_CONFIRM_ISOLATED_SYNTHETIC_SQLITE_ONLY'
            or os.getenv('DATABASE_URL')
            or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL')):
        raise UnsafeScheduledBackup('Only isolated synthetic SQLite with no DB env allowed')
    temp = Path(tempfile.gettempdir()).resolve()
    source, dest, password = map(Path, (source, destination, secret_file))
    # Resolve parents but REJECT symlinks rather than following them.
    if any(p.is_symlink() for p in (source, dest, password)):
        raise UnsafeScheduledBackup('Symlinks forbidden')
    if (not source.is_file() or source.suffix != '.db'
            or not source.name.startswith('beershop-v95-synthetic-')
            or not source.resolve().is_relative_to(temp)
            or not dest.is_dir() or not dest.resolve().is_relative_to(temp)):
        raise UnsafeScheduledBackup('Backup paths must point into isolated temp fixture')
    if source.stat().st_mode & 0o077 or dest.stat().st_mode & 0o077:
        raise UnsafeScheduledBackup('Local test database and archive folder must be private')
    if dest.resolve() == source.parent.resolve():
        raise UnsafeScheduledBackup('Backups must use a separate private directory')
    if (not password.is_file() or password.stat().st_mode & 0o077
            or not 0 < password.stat().st_size <= 1024):
        raise UnsafeScheduledBackup('Key file must be private and nonempty')
    if password.resolve() == source.resolve() or password.resolve().parent == dest.resolve():
        raise UnsafeScheduledBackup('Keep private key outside archive directory')
    return source, dest, password


def _read_passphrase(password_path):
    fd = os.open(str(password_path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise UnsafeScheduledBackup('Backup key file is not private')
        with os.fdopen(fd, 'rb') as handle:
            data = handle.read(1025)
    except BaseException:
        # fdopen closes fd only if it was successfully constructed.
        raise
    if not data or len(data) > 1024:
        raise UnsafeScheduledBackup('Backup key file is invalid')
    try:
        return data.decode('utf-8').rstrip('\n')
    except UnicodeError:
        raise UnsafeScheduledBackup('Backup key file encoding invalid') from None


def _sqlite_snapshot(source, metadata):
    # Read-only SQLite source; SQLite backup API gives one consistent snapshot.
    uri = 'file:' + quote(str(source), safe='/') + '?mode=ro'
    original = sqlite3.connect(uri, uri=True, timeout=5)
    memory = sqlite3.connect(':memory:')
    try:
        original.backup(memory)
        engine = create_engine('sqlite://', creator=lambda: memory,
                               poolclass=StaticPool)
        try:
            with engine.connect() as connection:
                inventory = schema_inventory(connection, metadata)
                if not inventory['schema_matches_columns']:
                    raise UnsafeScheduledBackup('Fixture schema has drift')
                rows = {table.name: [dict(row._mapping)
                       for row in connection.execute(select(table)).all()]
                       for table in metadata.sorted_tables}
        finally:
            engine.dispose()
    finally:
        original.close()
        memory.close()
    payload = add_integrity({
        'format':'beershop-snapshot-v1',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'tables':rows,
    }, metadata)
    verify_snapshot(payload, metadata)
    return json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')


def run_once(source, destination, secret_file, metadata, *, keep_days=14):
    if not isinstance(keep_days, int) or not (7 <= keep_days <= 365):
        raise UnsafeScheduledBackup('Retention must be 7–365 days')
    source, dest, password_path = validate_synthetic_paths(
        source, destination, secret_file)
    passphrase = _read_passphrase(password_path)
    # File lock prevents overlapping cron / service restarts.
    lock_path = dest / '.v95-backup.lock'
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR
                 | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_mode & 0o077:
            raise UnsafeScheduledBackup('Lock file permissions unsafe')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = _sqlite_snapshot(source, metadata)
        encrypted = encrypt_archive(raw, passphrase)
        # Verify before publishing a file. Never put plaintext on disk.
        if decrypt_archive(encrypted, passphrase) != raw:
            raise UnsafeScheduledBackup('Self-check failed')
        name = (datetime.now(timezone.utc).strftime('v95-%Y%m%dT%H%M%SZ-')
                + secrets.token_hex(8) + '.bse')
        path = dest / name
        write_private_exclusive(path, encrypted)
        # Only the named, tool-created ciphertext files older than retention.
        now = datetime.now(timezone.utc).timestamp()
        removed = 0
        for item in dest.iterdir():
            if (ARCHIVE_NAME.fullmatch(item.name) and not item.is_symlink()
                    and item.is_file() and
                    (now - item.stat().st_mtime) > keep_days * 86400):
                item.unlink()
                removed += 1
        return {'encrypted_backup_written': True,
                'encryption': 'AES-256-GCM',
                'retention_days': keep_days,
                'pruned_archives': removed,
                'stored_ciphertext_bytes': len(encrypted),
                'mode': 'SYNTHETIC_LOCAL_SQLITE_ONLY'}
    finally:
        os.close(fd)
