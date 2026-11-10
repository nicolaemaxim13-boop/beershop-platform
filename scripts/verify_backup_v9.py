#!/usr/bin/env python3
"""Offline BeerShop backup verification (never contacts a server or a bank).

Usage:
  python scripts/verify_backup_v9.py /local/private/backup.json
  python scripts/verify_backup_v9.py /local/private/backup.json --rehearse

This does NOT make backups encrypted, signed, or sufficient for production DR.
It verifies the archive's checksum, schema and counts. --rehearse temporarily
restores only into a NEW local SQLite file and deletes it after validation.
Do not run this against production database credentials.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.backup_v9 import MAX_ARCHIVE_SIZE, UnsafeBackup, verify_snapshot, rehearsal_restore_new_sqlite
from app.main import Base


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline BeerShop snapshot integrity and temporary SQLite restore rehearsal")
    parser.add_argument("archive", type=Path, help="Private v9-exported JSON snapshot on this device")
    parser.add_argument("--rehearse", action="store_true",
                        help="Reconstruct in a fresh temporary SQLite database and discard it")
    args = parser.parse_args(argv)
    try:
        path = args.archive
        if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_SIZE:
            raise UnsafeBackup("Snapshot is missing, not a regular file, or exceeds 25 MiB")
        raw = path.read_bytes()
        report = verify_snapshot(raw, Base.metadata)
        if args.rehearse:
            with tempfile.TemporaryDirectory(prefix="beershop-v9-rehearsal-") as temp:
                target = Path(temp) / "rehearsal.db"
                result = rehearsal_restore_new_sqlite(raw, Base.metadata, target)
                if not result.get("ok"):
                    raise UnsafeBackup("Local rehearsal did not complete")
        # Never display customer data, merchant identifiers, private paths or
        # database credentials in stdout or stderr. Counts are sufficient.
        print(json.dumps({
            "verified": True,
            "local_restore_rehearsed": bool(args.rehearse),
            "format": report["format"],
            "tables": len(report["counts"]),
            "rows": report["total_rows"],
            "sha256": report["sha256"],
            "note": "Integrity is not authenticity or encryption. Store private archives securely."
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (UnsafeBackup, OSError, ValueError, KeyError) as exc:
        # Some exceptions contain caller-controlled path or data; do not echo them.
        print("BeerShop offline backup verification failed; no database changes made.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
