"""v9.6 authenticated encrypted -> isolated PostgreSQL restore drill.

No plaintext archive on disk, no real credentials, no production or Neon writes.
The only allowed target is the NEW empty local DB beershop_v96_encrypted_restore.
"""
import os
from .backup_v9 import UnsafeBackup, verify_snapshot
from .secure_backup_v94 import decrypt_archive, UnsafeEncryptedBackup
from .pg_rehearsal_v94 import (
    ENCRYPTED_REHEARSAL_DB_NAME,
    validate_local_rehearsal_target,
    rehearsal_restore_empty_postgres,
)


def rehearsal_restore_encrypted_postgres(envelope, passphrase, metadata, target_url):
    # Validate destination before spending CPU on password derivation or reading data.
    if (os.getenv('BEERSHOP_V96_ENCRYPTED_RESTORE_ACK')
            != 'ISOLATED_ENCRYPTED_RESTORE_ONLY'
            or os.getenv('DATABASE_URL')
            or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL')):
        raise UnsafeBackup('Encrypted restore requires isolated test-only configuration')
    validate_local_rehearsal_target(target_url,
                                    expected_name=ENCRYPTED_REHEARSAL_DB_NAME)
    data = decrypt_archive(envelope, passphrase)
    # Authenticated encryption first, then application manifest/schema validation.
    report = verify_snapshot(data, metadata)
    restored = rehearsal_restore_empty_postgres(
        data, metadata, target_url,
        expected_name=ENCRYPTED_REHEARSAL_DB_NAME)
    if restored['sha256'] != report['sha256']:
        raise UnsafeBackup('Encrypted restore checksum verification failed')
    # Never echo passphrase, plaintext buyer PII, raw archive or database URL.
    return {
        'ok': True,
        'mode': 'ENCRYPTED_LOCAL_PG_REHEARSAL_ONLY',
        'authenticated_encryption': 'AES-256-GCM',
        'tables': restored['tables'],
        'rows': restored['rows'],
        'sha256': report['sha256'],
    }
