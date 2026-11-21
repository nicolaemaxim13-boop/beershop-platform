#!/usr/bin/env python3
"""Offline, read-only BeerShop v10.0 schema adoption report (NO_GO only).

Synthetic target: BEERSHOP_V96_PREFLIGHT_URL, with v9.6 isolated fixture ACK.
Does not accept URLs or secrets as command-line arguments.
Exit 3 means *manual review required*, NOT a failed data migration.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    # Explicitly refuse any known production connection even if an isolated
    # fixture URL is set. No attempt to open a remote database can follow.
    if os.getenv('DATABASE_URL') or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL'):
        print('v10 adoption report refused: live DB configuration present.', file=sys.stderr)
        return 2
    target = os.getenv('BEERSHOP_V96_PREFLIGHT_URL', '')
    if not target:
        print('v10 adoption report refused: isolated fixture URL missing.', file=sys.stderr)
        return 2
    try:
        from app.main import Base
        from app.adoption_plan_v100 import inspect_isolated_existing_plan
        report = inspect_isolated_existing_plan(target, Base.metadata)
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
        return 3  # A review-only NO_GO report must never be a success exit.
    except Exception:
        # Never disclose connection strings, SQL exception text or row contents.
        print('v10 adoption report could not inspect the isolated fixture.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(run())
