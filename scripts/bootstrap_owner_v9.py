#!/usr/bin/env python3
"""Bootstrap exactly one local staging owner; never touch a network DB.

Example (on isolated staging host):
  export DATABASE_URL=sqlite:////tmp/beershop-v9-staff-staging.db
  export BEERSHOP_V9_STAFF_AUTH=1
  export BEERSHOP_V9_MFA_KEY="<Fernet key from a secure secret manager>"
  python scripts/bootstrap_owner_v9.py --username demoowner

Password is requested interactively and the TOTP enrollment URI is emitted
exactly once on the local terminal. Neither password nor seed is logged.
"""
import argparse
import getpass
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bootstrap one local v9 staging owner")
    parser.add_argument('--username', required=True,
                        help="Lowercase account ID (3–32 characters)")
    args = parser.parse_args(argv)
    url = os.getenv('DATABASE_URL', '')
    temp_dir = Path(tempfile.gettempdir()).resolve()
    # Only absolute SQLite database paths inside local temp are supported.
    prefix = 'sqlite:////'
    if (os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL') or
        os.getenv('BEERSHOP_V9_STAFF_AUTH') != '1' or
        not url.startswith(prefix) or url.endswith(':memory:')):
        print('Refusing: requires opt-in staging and isolated local SQLite database.',
              file=sys.stderr)
        return 2
    raw_path = Path(url.removeprefix('sqlite:///')).resolve()
    if raw_path.suffix != '.db' or not raw_path.is_relative_to(temp_dir) or raw_path.is_symlink():
        print('Refusing: DB must be a local .db file in a temp directory.',
              file=sys.stderr)
        return 2
    from app.staff_v9 import require_staging
    require_staging()
    from app.main import Base, SessionLocal, engine, StaffUserV9, create_staff_user_v9
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.query(StaffUserV9).count():
            print('Refusing: staff accounts already exist. Use an owner session.',file=sys.stderr)
            return 3
        password = getpass.getpass('Staging owner password (14+ characters): ')
        repeated = getpass.getpass('Confirm staging owner password: ')
        if password != repeated or len(password) < 14:
            print('Password confirmation failed.', file=sys.stderr)
            return 2
        try:
            user, uri = create_staff_user_v9(db, args.username, password, 'owner')
        except ValueError:
            print('Invalid owner account.',file=sys.stderr)
            return 2
    print('Created isolated staging owner:',user.username)
    print('Enroll MFA immediately. This URI will not be displayed again.')
    print(uri)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
