"""Offline-only BeerShop backup checker / recovery rehearsal.

Examples:
  python scripts/backup_rehearsal_v9.py private-backup.json
  python scripts/backup_rehearsal_v9.py private-backup.json --restore-to /tmp/beershop-restore-new.db

Never supply production DB variables or real Neon/Render URLs. The input
contains personal data: keep it in a private, encrypted local directory.
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Execute from repository root without needing an installed package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description='Verify BeerShop snapshot integrity offline')
    parser.add_argument('backup', type=Path, help='A local private JSON backup')
    parser.add_argument('--restore-to', type=Path, default=None,
                        help='A NEW .db path under /tmp (never existing production data)')
    args = parser.parse_args()

    # Fail closed before app.main imports engine configuration.
    if os.environ.get('BEERSHOP_PERSISTENT_DATABASE_URL') or os.environ.get('DATABASE_URL'):
        parser.error('Unset production database environment variables before offline verification')
    if args.backup.is_symlink() or not args.backup.is_file():
        parser.error('Backup must be an ordinary, existing, private local file')

    from app.main import Base
    from app.backup_v9 import (MAX_ARCHIVE_SIZE, UnsafeBackup,
                               verify_snapshot, rehearsal_restore_new_sqlite)
    try:
        with args.backup.open('rb') as f:
            data = f.read(MAX_ARCHIVE_SIZE + 1)
        report = verify_snapshot(data, Base.metadata)
        output = {'verified': True, 'manifest_sha256': report['sha256'],
                  'schema_sha256': report['schema_sha256'],
                  'total_rows': report['total_rows'],
                  'counts': report['counts']}
        if args.restore_to is not None:
            restore = rehearsal_restore_new_sqlite(data, Base.metadata, args.restore_to)
            output.update({'restore_rehearsal': restore['mode'], 'restored': True})
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    except (UnsafeBackup, ValueError, OSError, RuntimeError) as exc:
        # Never echo source file contents, customer records or token values.
        print('Backup verification failed: ' + type(exc).__name__, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
