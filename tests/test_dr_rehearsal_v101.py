"""v10.1 synthetic encrypted SQLite disaster-recovery smoke/negative tests.

No production database URLs, customer data, cloud services or external calls.
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from app.backup_v9 import (
    UnsafeBackup, add_integrity, rehearsal_restore_new_sqlite, verify_snapshot,
)
from app.secure_backup_v94 import (
    UnsafeEncryptedBackup, decrypt_archive, encrypt_archive,
)
from app.main import Base, Store

PASSWORD = "synthetic-v101-disaster-recovery-test-password"


def synthetic_archive():
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(Store.__table__.insert(), {
                "id": 1, "slug": "synthetic", "name": "SYNTHETIC_V101_ONLY",
            })
            tables = {
                table.name: [dict(row._mapping) for row in conn.execute(select(table))]
                for table in Base.metadata.sorted_tables
            }
        payload = add_integrity({
            "format": "beershop-snapshot-v1",
            "created_at": "2026-11-20T12:00:00+00:00",
            "tables": tables,
        }, Base.metadata)
        return json.dumps(payload, ensure_ascii=False, default=str).encode()
    finally:
        engine.dispose()


def test_encrypted_restore_checks_counts_schema_and_invariants(tmp_path):
    raw = synthetic_archive()
    encrypted = encrypt_archive(raw, PASSWORD)
    assert b"SYNTHETIC_V101_ONLY" not in encrypted
    authenticated = decrypt_archive(encrypted, PASSWORD)
    manifest = verify_snapshot(authenticated, Base.metadata)
    destination = tmp_path / "new-recovery.db"
    result = rehearsal_restore_new_sqlite(authenticated, Base.metadata, destination)
    assert result["mode"] == "LOCAL_REHEARSAL_ONLY"
    assert result["checks"]["sha256"] == manifest["sha256"]
    assert result["checks"]["schema_sha256"] == manifest["schema_sha256"]
    engine = create_engine("sqlite:///" + str(destination))
    try:
        with engine.connect() as conn:
            actual = {
                table.name: [dict(row._mapping) for row in conn.execute(select(table))]
                for table in Base.metadata.sorted_tables
            }
        restored = add_integrity({
            "format": "beershop-snapshot-v1",
            "created_at": "2026-11-20T12:00:00+00:00",
            "tables": actual,
        }, Base.metadata)
        assert verify_snapshot(restored, Base.metadata)["counts"] == manifest["counts"]
        assert verify_snapshot(restored, Base.metadata)["sha256"] == manifest["sha256"]
    finally:
        engine.dispose()


def test_wrong_key_and_tamper_never_create_restore_target(tmp_path):
    encrypted = encrypt_archive(synthetic_archive(), PASSWORD)
    target = tmp_path / "never-created.db"
    with pytest.raises(UnsafeEncryptedBackup):
        decrypt_archive(encrypted, "incorrect-synthetic-recovery-password")
    changed = bytearray(encrypted)
    changed[-1] ^= 1
    with pytest.raises(UnsafeEncryptedBackup):
        decrypt_archive(bytes(changed), PASSWORD)
    assert not target.exists()


def test_missing_table_and_modified_manifest_rejected_before_file_creation(tmp_path):
    payload = json.loads(synthetic_archive())
    payload["tables"].pop("stores")
    target = tmp_path / "missing.db"
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_new_sqlite(payload, Base.metadata, target)
    assert not target.exists()
    payload = json.loads(synthetic_archive())
    payload["integrity"]["sha256"] = "0" * 64
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_new_sqlite(payload, Base.metadata, target)
    assert not target.exists()


def test_existing_target_and_non_temp_destination_refused(tmp_path):
    raw = synthetic_archive()
    target = tmp_path / "existing.db"
    target.write_bytes(b"DO-NOT-OVERWRITE")
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_new_sqlite(raw, Base.metadata, target)
    assert target.read_bytes() == b"DO-NOT-OVERWRITE"
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_new_sqlite(raw, Base.metadata, Path("/var/lib/beershop-v101.db"))
