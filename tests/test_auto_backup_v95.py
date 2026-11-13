"""v9.5 encrypted synthetic backup timer: never touches live DBs."""
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select

import app.main as main
from app.auto_backup_v95 import run_once, UnsafeScheduledBackup
from app.secure_backup_v94 import decrypt_archive
from app.backup_v9 import verify_snapshot

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "auto_backup_v95.py"
PASSWORD = "synthetic-scheduled-backup-key-only-2026"


def staging_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("BEERSHOP_V95_SYNTHETIC_BACKUP_ACK",
                       "I_CONFIRM_ISOLATED_SYNTHETIC_SQLITE_ONLY")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BEERSHOP_PERSISTENT_DATABASE_URL", raising=False)
    source = tmp_path / ("beershop-v95-synthetic-" + uuid4().hex[:8] + ".db")
    private = tmp_path / "archives"
    private.mkdir(mode=0o700)
    key = tmp_path / "private-key"
    key.write_text(PASSWORD, encoding="utf-8")
    key.chmod(0o600)
    eng = create_engine("sqlite:///" + str(source))
    main.Base.metadata.create_all(eng)
    with eng.begin() as db:
        db.execute(main.Store.__table__.insert(), {
            "id":1, "slug":"beershop", "name":"SYNTHETIC-DO-NOT-USE-REAL"})
    eng.dispose()
    source.chmod(0o600)
    return source, private, key


def test_auto_backup_encrypts_without_intermediate_plaintext_and_prunes(tmp_path, monkeypatch):
    source, directory, secret_file = staging_fixture(tmp_path, monkeypatch)
    result = run_once(source, directory, secret_file, main.Base.metadata, keep_days=7)
    assert result["encrypted_backup_written"]
    assert result["mode"] == "SYNTHETIC_LOCAL_SQLITE_ONLY"
    files = list(directory.glob("v95-*.bse"))
    assert len(files) == 1
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert b"SYNTHETIC-DO-NOT-USE-REAL" not in files[0].read_bytes()
    raw = decrypt_archive(files[0].read_bytes(), PASSWORD)
    assert verify_snapshot(raw, main.Base.metadata)["ok"]
    assert b"SYNTHETIC-DO-NOT-USE-REAL" in raw
    assert not list(directory.glob("*.json"))
    assert not list(tmp_path.glob("*.json"))
    old = time.time() - 9*86400
    os.utime(files[0], (old,old))
    harmless = directory / "operator-not-a-v95-backup.bse"
    harmless.write_text("never delete this", encoding="utf-8")
    os.utime(harmless, (old, old))
    second = run_once(source, directory, secret_file, main.Base.metadata, keep_days=7)
    assert second["pruned_archives"] == 1
    assert len(list(directory.glob("v95-*.bse"))) == 1
    assert harmless.read_text() == "never delete this"
    with pytest.raises(UnsafeScheduledBackup):
        run_once(source, directory, secret_file, main.Base.metadata, keep_days=1)


def test_auto_backup_fail_closed_and_never_overwrites_private_files(tmp_path, monkeypatch):
    source, directory, secret_file = staging_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL","postgresql://fake:fake@ep-neon.neon.tech/live")
    with pytest.raises(UnsafeScheduledBackup):
        run_once(source, directory, secret_file, main.Base.metadata)
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.delenv("BEERSHOP_V95_SYNTHETIC_BACKUP_ACK")
    with pytest.raises(UnsafeScheduledBackup):
        run_once(source, directory, secret_file, main.Base.metadata)
    monkeypatch.setenv("BEERSHOP_V95_SYNTHETIC_BACKUP_ACK",
                       "I_CONFIRM_ISOLATED_SYNTHETIC_SQLITE_ONLY")
    secret_file.chmod(0o644)
    with pytest.raises(UnsafeScheduledBackup):
        run_once(source, directory, secret_file, main.Base.metadata)
    secret_file.chmod(0o600)
    invalid = tmp_path / "not-synthetic.db"
    invalid.write_bytes(source.read_bytes())
    invalid.chmod(0o600)
    with pytest.raises(UnsafeScheduledBackup):
        run_once(invalid, directory, secret_file, main.Base.metadata)
    spy = tmp_path / "fake-symlink.db"
    spy.symlink_to(source)
    with pytest.raises(UnsafeScheduledBackup):
        run_once(spy, directory, secret_file, main.Base.metadata)


def test_timer_cli_once_is_private(tmp_path, monkeypatch):
    source, directory, secret_file = staging_fixture(tmp_path, monkeypatch)
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.pop("BEERSHOP_PERSISTENT_DATABASE_URL", None)
    env["BEERSHOP_V95_SYNTHETIC_BACKUP_ACK"] = (
        "I_CONFIRM_ISOLATED_SYNTHETIC_SQLITE_ONLY")
    p = subprocess.run(
        [sys.executable, str(CLI), "--source", str(source),
         "--destination", str(directory), "--key-file", str(secret_file),
         "--keep-days", "14"], cwd=ROOT, env=env,
        text=True, capture_output=True, timeout=90, check=False)
    assert p.returncode == 0, p.stderr
    assert "SYNTHETIC_LOCAL_SQLITE_ONLY" in p.stdout
    assert PASSWORD not in p.stdout + p.stderr
    assert str(secret_file) not in p.stdout + p.stderr
    assert len(list(directory.glob("v95-*.bse"))) == 1
    env["DATABASE_URL"] = "postgresql://fake:fake@ep-neon.neon.tech/live"
    rejected = subprocess.run(
        [sys.executable, str(CLI), "--source", str(source),
         "--destination", str(directory), "--key-file", str(secret_file)],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
        check=False)
    assert rejected.returncode != 0
    assert "Traceback" not in rejected.stderr
    assert len(list(directory.glob("v95-*.bse"))) == 1
