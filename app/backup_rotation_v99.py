"""v9.9 synthetic-only encrypted-backup key rotation drill.

There is no production backup scheduler and no production/Neon key access.
This module never stores plaintext to disk, never deletes an existing archive,
and refuses overwrite/symlink destinations.
"""
import os
import re
import stat
import tempfile
from pathlib import Path

from .backup_v9 import verify_snapshot
from .secure_backup_v94 import (
    MAX_ENCRYPTED, decrypt_archive, encrypt_archive, write_private_exclusive,
)

SYNTHETIC_FILE = re.compile(r'^beershop-v99-synthetic-[a-zA-Z0-9_-]{1,48}\.bse$')
ACK = 'LOCAL_SYNTHETIC_KEY_ROTATION_ONLY'


class UnsafeRotation(ValueError):
    pass


def validate_fixture_path(path, *, must_exist):
    candidate = Path(path)
    root = Path(tempfile.gettempdir()).resolve()
    if (os.environ.get('DATABASE_URL')
            or os.environ.get('BEERSHOP_PERSISTENT_DATABASE_URL')
            or os.environ.get('BEERSHOP_V99_ROTATION_ACK') != ACK):
        raise UnsafeRotation('Only isolated synthetic offline archive rotation allowed')
    if (not SYNTHETIC_FILE.fullmatch(candidate.name)
            or candidate.is_symlink()
            or candidate.parent.is_symlink()
            or not candidate.parent.is_dir()
            or not candidate.parent.resolve().is_relative_to(root)
            or candidate.parent.stat().st_mode & 0o077):
        raise UnsafeRotation('Synthetic private temp archive path required')
    if must_exist:
        if (not candidate.is_file()
                or candidate.stat().st_mode & 0o077
                or candidate.stat().st_size > MAX_ENCRYPTED):
            raise UnsafeRotation('Source encrypted archive must be private and bounded')
    elif candidate.exists() or candidate.is_symlink():
        raise UnsafeRotation('Destination must be a new absent archive')
    return candidate


def rotate_synthetic_archive(source_path, destination_path, old_passphrase,
                             new_passphrase, metadata):
    source = validate_fixture_path(source_path, must_exist=True)
    dest = validate_fixture_path(destination_path, must_exist=False)
    if source.resolve() == dest.resolve() or old_passphrase == new_passphrase:
        raise UnsafeRotation('Must use new path and distinct passphrases')
    fd = os.open(str(source), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_mode & 0o077:
            raise UnsafeRotation('Source permissions unsafe')
        with os.fdopen(fd, 'rb') as handle:
            envelope = handle.read(MAX_ENCRYPTED + 1)
    except BaseException:
        # The open handle was owned by fdopen when construction succeeded.
        raise
    if len(envelope) > MAX_ENCRYPTED:
        raise UnsafeRotation('Encrypted archive too large')
    raw = decrypt_archive(envelope, old_passphrase)
    checked = verify_snapshot(raw, metadata)
    replacement = encrypt_archive(raw, new_passphrase)
    if decrypt_archive(replacement, new_passphrase) != raw:
        raise UnsafeRotation('Post-rotation authentication failed')
    # Never overwrite the original and never write decrypted content to disk.
    write_private_exclusive(dest, replacement)
    return {
        'mode': 'SYNTHETIC_OFFLINE_ROTATION_ONLY',
        'authenticated_encryption': 'AES-256-GCM',
        'rekeyed': True,
        'source_preserved': True,
        'new_archive_private': True,
        'rows': checked['total_rows'],
        'schema_sha256': checked['schema_sha256'],
    }
