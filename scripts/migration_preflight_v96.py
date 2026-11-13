#!/usr/bin/env python3
"""Read-only migration audit of an existing, expressly isolated fixture.

Never reads or writes a production/Neon DB; prints no customer rows or URLs.
Run only on a disposable local fixture after explicitly configuring:
BEERSHOP_V96_PREFLIGHT_ACK=ISOLATED_READONLY_PREFLIGHT
BEERSHOP_V96_PREFLIGHT_URL=sqlite:////tmp/private/beershop-v96-preflight-demo.db
and unset DATABASE_URL / BEERSHOP_PERSISTENT_DATABASE_URL.
"""
import json
import os
import sys
from pathlib import Path

if os.environ.get('DATABASE_URL') or os.environ.get('BEERSHOP_PERSISTENT_DATABASE_URL'):
    print('Refusing preflight: production database settings present.', file=sys.stderr)
    raise SystemExit(2)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    try:
        from app.main import Base
        from app.migration_preflight_v96 import (
            inspect_isolated_existing, UnsafeMigrationPreflight)
        result = inspect_isolated_existing(
            os.getenv('BEERSHOP_V96_PREFLIGHT_URL', ''), Base.metadata)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        # Drift is not a reason to run migrations; reviewers must reconcile it.
        return 0 if result['existing_schema_matches_model'] else 3
    except (Exception) as error:
        # Omit caller-controlled paths, DB credentials and raw DB exception text.
        print('Isolated schema preflight refused or failed.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(run())
