#!/usr/bin/env python3
"""Emit a DDL-only SQLite/PostgreSQL schema contract (no rows, no DB I/O)."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(argv=None):
    parser = argparse.ArgumentParser(description='Model DDL contract, NOT migration execution')
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--check-lock', type=Path, default=None)
    args = parser.parse_args(argv)
    from app.main import Base
    from app.schema_contract_v98 import ddl_contract
    result = ddl_contract(Base.metadata)
    if args.check_lock:
        # Read only a fixed schema lock in the checked-out repository.
        lock = args.check_lock.resolve()
        if lock != (ROOT / 'migrations_v95' / 'schema_v98.lock.json').resolve():
            print('Schema lock path not allowed', file=sys.stderr)
            return 2
        try:
            expected = json.loads(lock.read_text(encoding='utf-8'))
            if (expected.get('format') != result['format']
                    or expected.get('table_count') != result['table_count']
                    or expected.get('sha256') != result['sha256']):
                print('Model schema drift: manual migration review required',
                      file=sys.stderr)
                return 3
        except (OSError, ValueError, TypeError):
            print('Schema lock missing or invalid', file=sys.stderr)
            return 3
    if args.output:
        try:
            fd = os.open(str(args.output), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, sort_keys=True, indent=2)
                f.write('\n')
        except OSError:
            print('Could not safely create schema contract file', file=sys.stderr)
            return 2
    # Print digest only, never database URL or metadata containing rows.
    print('BeerShop v9.8 model DDL SHA256=' + result['sha256'] +
          ' tables=' + str(result['table_count']))
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
