"""BeerShop v9.8 owner-only, encrypted HTTP backup pilot.

The whole feature is strictly limited to the named synthetic CI databases.
Unlike v9.4's local CLI, this encrypts before any response body is emitted.
No plaintext JSON is returned or written as an intermediate file.
"""
import json
import os
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy import select

from .backup_v9 import add_integrity, verify_snapshot
from .secure_backup_v94 import encrypt_archive, UnsafeEncryptedBackup

ACK = 'SYNTHETIC_OWNER_MFA_ENCRYPTED_EXPORT_ONLY'
SYNTHETIC_SQLITE = 'beershop_ci_test.db'
SYNTHETIC_POSTGRES = 'beershop_test'


def enabled():
    return os.getenv('BEERSHOP_V98_ENCRYPTED_EXPORT') == '1'


def require_synthetic_encrypted_export(*, database_url, persistent_url):
    """Fail closed before reading any records or building an archive."""
    if not enabled():
        raise HTTPException(503, 'Encrypted staging export disabled')
    if (persistent_url
            or os.getenv('BEERSHOP_V98_EXPORT_ACK') != ACK
            or os.getenv('BEERSHOP_V91_STAGING_ACK') != 'ISOLATED_STAGING_ONLY'
            or os.getenv('BEERSHOP_V91_ENFORCE_RBAC') != '1'
            or os.getenv('BEERSHOP_V9_STAFF_AUTH') != '1'):
        raise HTTPException(503, 'Encrypted export requires isolated owner-MFA staging')
    from .rbac_v91 import require_isolated_staging
    require_isolated_staging(persistent_url=persistent_url, database_url=database_url)
    try:
        parsed = make_url(database_url)
        kind = parsed.get_backend_name()
    except (ArgumentError, ValueError, TypeError):
        raise HTTPException(503, 'Unsafe encrypted export database') from None
    # A general localhost/SQLite exemption is NOT enough: accept only
    # disposable CI fixture names; no remote DB or unknown local path.
    if kind == 'sqlite':
        from pathlib import Path
        if (parsed.host or Path(parsed.database or '').name != SYNTHETIC_SQLITE
                or parsed.database == ':memory:'):
            raise HTTPException(503, 'Export must use synthetic CI SQLite fixture')
    elif kind == 'postgresql':
        if (parsed.host not in ('127.0.0.1', 'localhost', '::1')
                or parsed.database != SYNTHETIC_POSTGRES
                or os.getenv('BEERSHOP_V91_ALLOW_LOCAL_POSTGRES') != '1'):
            raise HTTPException(503, 'Export must use synthetic CI PostgreSQL fixture')
    else:
        raise HTTPException(503, 'Unsupported synthetic fixture type')
    secret = os.getenv('BEERSHOP_V98_EXPORT_PASSPHRASE', '')
    if len(secret) < 32 or len(secret) > 1024 or not secret.strip():
        raise HTTPException(503, 'Encrypted export key not configured')
    return secret


def encrypted_snapshot_from_session(db, metadata, passphrase):
    """Creates an authenticated v9 archive and encrypts it entirely in RAM.

    Caller must validate isolation and owner MFA first. No data is logged.
    """
    snapshot = {
        'format': 'beershop-snapshot-v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'tables': {
            table.name: [
                dict(row._mapping) for row in db.execute(select(table)).all()
            ]
            for table in metadata.sorted_tables
        },
    }
    snapshot = add_integrity(snapshot, metadata)
    raw = json.dumps(snapshot, ensure_ascii=False, default=str).encode('utf-8')
    verify_snapshot(raw, metadata)
    try:
        return encrypt_archive(raw, passphrase)
    except UnsafeEncryptedBackup:
        # Do not reveal data/credentials or emit plaintext on encryption failure.
        raise HTTPException(503, 'Encrypted export unavailable') from None
