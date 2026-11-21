#!/usr/bin/env python3
"""Rotate a *synthetic test archive* without writing plaintext to disk.

Requires BEERSHOP_V99_ROTATION_ACK=LOCAL_SYNTHETIC_KEY_ROTATION_ONLY.
Archives must be private /tmp/beershop-v99-synthetic-*.bse files.
Neither old nor new passphrase is accepted on the command line or env.
"""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

if os.getenv('DATABASE_URL') or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL'):
    print('Offline fixture rotation refused: database environment is configured.',
          file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(argv=None):
    parser = argparse.ArgumentParser(description='Synthetic archive passphrase rotation')
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args(argv)
    from app.main import Base
    from app.backup_rotation_v99 import rotate_synthetic_archive, UnsafeRotation
    from app.backup_v9 import UnsafeBackup
    from app.secure_backup_v94 import UnsafeEncryptedBackup
    try:
        old = getpass.getpass('Old synthetic archive passphrase: ')
        new = getpass.getpass('New synthetic archive passphrase: ')
        confirmation = getpass.getpass('Repeat new passphrase: ')
        if new != confirmation:
            raise UnsafeRotation('Confirmation mismatch')
        result = rotate_synthetic_archive(args.source, args.destination,
                                          old, new, Base.metadata)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (UnsafeRotation, UnsafeBackup, UnsafeEncryptedBackup,
            OSError, ValueError, EOFError):
        # No exception strings: they could contain filesystem paths or secrets.
        print('Synthetic-only archive rotation refused or failed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
