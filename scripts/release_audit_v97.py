#!/usr/bin/env python3
"""Local source-only BeerShop v9.7 release audit (NO_GO by design).

No DB connections, credentials, network requests or real customer records.
The report contains no secrets. --output writes a NEW private JSON file.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.release_gate_v97 import audit_repository, assert_staging_controls_present


def main(argv=None):
    parser = argparse.ArgumentParser(description='BeerShop conservative release gate')
    parser.add_argument('--output', type=Path, default=None,
                        help='Write a NEW 0600 JSON report; do not overwrite existing reports')
    parser.add_argument('--assert-no-go', action='store_true',
                        help='Fail when the release gate is unexpectedly marked ready')
    parser.add_argument('--assert-staging-controls', action='store_true',
                        help='Fail if expected source-level staging guards disappeared')
    args = parser.parse_args(argv)
    result = audit_repository(ROOT)
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    if args.output is not None:
        try:
            # Exclusive create avoids replacing a prior security review.
            fd = os.open(str(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            print('Cannot create private release audit report.', file=sys.stderr)
            return 2
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(payload)
        except OSError:
            args.output.unlink(missing_ok=True)
            print('Cannot write private release audit report.', file=sys.stderr)
            return 2
        print('BeerShop release gate: NO_GO report generated (no secrets read).')
    else:
        print(payload, end='')
    if args.assert_no_go and (result['production_ready']
                               or result['decision'] != 'NO_GO'
                               or result['blocker_count'] == 0):
        return 3
    if args.assert_staging_controls and not assert_staging_controls_present(result):
        print('Required staging safeguard source checks missing.', file=sys.stderr)
        return 4
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
