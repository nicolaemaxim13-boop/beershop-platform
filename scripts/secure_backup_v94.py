#!/usr/bin/env python3
"""Local, encrypted BeerShop v9.4 backup transport and offline verification.

No network requests, no real production DB, no plaintext restore on disk unless
--rehearse explicitly creates temporary SQLite in a temporary directory.
Usage: python scripts/secure_backup_v94.py encrypt private.json private.bse
       python scripts/secure_backup_v94.py verify private.bse [--rehearse]
Passphrases are prompted invisibly (never CLI argv or env variables).
"""
import argparse
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path

# Fail closed before importing any module that configures a SQL engine.
if os.environ.get('DATABASE_URL') or os.environ.get('BEERSHOP_PERSISTENT_DATABASE_URL'):
    print('Offline backup tool requires database environment variables to be unset.',
          file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.secure_backup_v94 import (
    MAX_ENCRYPTED, MAX_PLAINTEXT, UnsafeEncryptedBackup,
    encrypt_archive, decrypt_archive, write_private_exclusive)
from app.backup_v9 import verify_snapshot, rehearsal_restore_new_sqlite, UnsafeBackup
from app.main import Base


def private_read(path, max_size):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_size:
        raise UnsafeEncryptedBackup('Invalid input archive')
    with path.open('rb') as handle:
        return handle.read(max_size + 1)


def run(argv=None):
    parser = argparse.ArgumentParser(description='Offline private BeerShop encrypted backups')
    sub = parser.add_subparsers(dest='operation', required=True)
    enc = sub.add_parser('encrypt')
    enc.add_argument('source', type=Path, help='Private plaintext v9 archive to encrypt')
    enc.add_argument('destination', type=Path, help='A NEW .bse encrypted file, never overwrite')
    verify = sub.add_parser('verify')
    verify.add_argument('source', type=Path, help='Encrypted .bse archive')
    verify.add_argument('--rehearse', action='store_true',
                        help='Restore temporarily to isolated SQLite, then discard')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'encrypt':
            if args.destination.suffix != '.bse':
                raise UnsafeEncryptedBackup('Destination must end in .bse')
            if args.destination.exists() or args.destination.is_symlink():
                raise UnsafeEncryptedBackup('Destination already exists')
            raw = private_read(args.source, MAX_PLAINTEXT)
            report = verify_snapshot(raw, Base.metadata)
            password = getpass.getpass('Encryption passphrase (20+ characters): ')
            repeated = getpass.getpass('Confirm encryption passphrase: ')
            if password != repeated:
                raise UnsafeEncryptedBackup('Passphrase mismatch')
            blob = encrypt_archive(raw, password)
            write_private_exclusive(args.destination, blob)
            # Plaintext input is NOT deleted automatically.
            print(json.dumps({'encrypted': True, 'format': 'BEERSHOP-V94-ENC',
                              'rows': report['total_rows'],
                              'plaintext_source_still_exists': True}, sort_keys=True))
        else:
            raw = private_read(args.source, MAX_ENCRYPTED)
            password = getpass.getpass('Decryption passphrase: ')
            data = decrypt_archive(raw, password)
            report = verify_snapshot(data, Base.metadata)
            if args.rehearse:
                with tempfile.TemporaryDirectory(prefix='beershop-v94-') as temporary:
                    destination = Path(temporary) / 'synthetic-rehearsal.db'
                    restored = rehearsal_restore_new_sqlite(data, Base.metadata, destination)
                    if not restored.get('ok'):
                        raise UnsafeBackup('Restore verification failed')
            print(json.dumps({'verified': True, 'authenticated_encryption': True,
                              'local_sqlite_restore': bool(args.rehearse),
                              'rows': report['total_rows'],
                              'sha256': report['sha256']}, sort_keys=True))
        return 0
    except (UnsafeEncryptedBackup, UnsafeBackup, OSError, ValueError, EOFError):
        # Do not echo exception: paths and other untrusted input could contain PII.
        print('Encrypted backup operation failed; no production DB operations performed.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
