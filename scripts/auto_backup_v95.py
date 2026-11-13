#!/usr/bin/env python3
"""v9.5 isolated staging backup timer.

No production DB, cloud database, network or payments.
Requires explicit confirmation flag and a synthetic /tmp SQLite .db.
Run once with a local cron job, or use --interval-minutes for an
operator-managed process. It is NOT automatically scheduled on Render.
Key must reside in its own private 0600 file outside the archive folder.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# Never import an app configured with a live data source.
if os.getenv('DATABASE_URL') or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL'):
    print('Refusing: database environment must be unset for isolated fixture.',
          file=sys.stderr)
    raise SystemExit(2)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import Base
from app.auto_backup_v95 import run_once, UnsafeScheduledBackup
from app.secure_backup_v94 import UnsafeEncryptedBackup
from app.backup_v9 import UnsafeBackup


def run(argv=None):
    parser = argparse.ArgumentParser(description='Synthetic local staging encrypted backups')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--keep-days', type=int, default=14)
    parser.add_argument('--interval-minutes', type=int, default=None,
                        help='Run repeatedly inside this foreground process; 60–1440')
    args = parser.parse_args(argv)
    if args.interval_minutes is not None and not (60 <= args.interval_minutes <= 1440):
        parser.error('Timer interval must be between 60 and 1440 minutes')
    try:
        while True:
            result = run_once(args.source, args.destination, args.key_file,
                              Base.metadata, keep_days=args.keep_days)
            print(json.dumps(result, sort_keys=True), flush=True)
            if args.interval_minutes is None:
                return 0
            time.sleep(args.interval_minutes * 60)
    except KeyboardInterrupt:
        return 0
    except (UnsafeScheduledBackup, UnsafeEncryptedBackup, UnsafeBackup,
            OSError, ValueError, RuntimeError):
        print('Staging backup declined or failed; no remote database accessed.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
