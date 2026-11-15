"""v9.9 key rotation of synthetic encrypted backups, no DB connection."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from app import main
from app.backup_v9 import add_integrity
from app.backup_rotation_v99 import rotate_synthetic_archive, UnsafeRotation, ACK
from app.secure_backup_v94 import (
    encrypt_archive, decrypt_archive, UnsafeEncryptedBackup,
    write_private_exclusive)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'rotate_backup_v99.py'
OLD = 'v99-synthetic-old-phrase-do-not-use-outside-tests'
NEW = 'v99-synthetic-new-phrase-do-not-use-outside-tests'


def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('BEERSHOP_V99_ROTATION_ACK', ACK)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    database = create_engine('sqlite:///:memory:')
    main.Base.metadata.create_all(database)
    with database.begin() as conn:
        conn.execute(main.Store.__table__.insert(), {
            'id': 1, 'slug': 'beershop',
            'name': 'V99_FAKE_BUYER_DATA_SHOULD_NOT_APPEAR_IN_ARCHIVE'})
        rows = {t.name: [dict(row._mapping) for row in conn.execute(select(t)).all()]
                for t in main.Base.metadata.sorted_tables}
    database.dispose()
    payload = add_integrity({
        'format': 'beershop-snapshot-v1',
        'created_at': '2026-11-15T12:00:00+00:00',
        'tables': rows,
    }, main.Base.metadata)
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode()
    oldfile = tmp_path/'beershop-v99-synthetic-old.bse'
    newfile = tmp_path/'beershop-v99-synthetic-new.bse'
    write_private_exclusive(oldfile, encrypt_archive(raw, OLD))
    return oldfile, newfile, raw


def test_v99_rotate_is_authenticated_private_exclusive_and_keeps_source(tmp_path, monkeypatch):
    source, destination, raw = fixture(tmp_path, monkeypatch)
    original = source.read_bytes()
    report = rotate_synthetic_archive(source, destination, OLD, NEW,
                                      main.Base.metadata)
    assert report['rekeyed'] is True
    assert report['source_preserved'] is True
    assert report['mode'] == 'SYNTHETIC_OFFLINE_ROTATION_ONLY'
    assert report['rows'] >= 1
    assert destination.exists()
    assert source.read_bytes() == original
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.read_bytes() != original
    assert b'V99_FAKE_BUYER_DATA' not in destination.read_bytes()
    assert decrypt_archive(destination.read_bytes(), NEW) == raw
    assert decrypt_archive(source.read_bytes(), OLD) == raw
    with pytest.raises(UnsafeEncryptedBackup):
        decrypt_archive(destination.read_bytes(), OLD)
    with pytest.raises(UnsafeRotation):
        rotate_synthetic_archive(source, destination, OLD, NEW,
                                 main.Base.metadata)
    assert not list(tmp_path.glob('*.json'))
    assert OLD not in str(report) and NEW not in str(report)


def test_v99_rotation_fails_closed_without_writing_destination(tmp_path, monkeypatch):
    source, dest, _ = fixture(tmp_path, monkeypatch)
    with pytest.raises(UnsafeEncryptedBackup):
        rotate_synthetic_archive(source, dest, 'wrong-old-passphrase-long-enough',
                                 NEW, main.Base.metadata)
    assert not dest.exists()
    with pytest.raises(UnsafeRotation):
        rotate_synthetic_archive(source, dest, OLD, OLD, main.Base.metadata)
    monkeypatch.setenv('DATABASE_URL',
                       'postgresql://fake:fake@ep-neon.neon.tech/live')
    with pytest.raises(UnsafeRotation):
        rotate_synthetic_archive(source, dest, OLD, NEW, main.Base.metadata)
    monkeypatch.delenv('DATABASE_URL')
    monkeypatch.delenv('BEERSHOP_V99_ROTATION_ACK')
    with pytest.raises(UnsafeRotation):
        rotate_synthetic_archive(source, dest, OLD, NEW, main.Base.metadata)
    monkeypatch.setenv('BEERSHOP_V99_ROTATION_ACK', ACK)
    unexpected = tmp_path/'non-synthetic.bse'
    with pytest.raises(UnsafeRotation):
        rotate_synthetic_archive(source, unexpected, OLD, NEW, main.Base.metadata)
    link = tmp_path/'beershop-v99-synthetic-link.bse'
    link.symlink_to(source)
    with pytest.raises(UnsafeRotation):
        rotate_synthetic_archive(link, dest, OLD, NEW, main.Base.metadata)
    assert not dest.exists()


def test_v99_rotation_tamper_is_detected_before_new_file(tmp_path, monkeypatch):
    source, dest, _ = fixture(tmp_path, monkeypatch)
    broken = bytearray(source.read_bytes())
    broken[-1] ^= 1
    source.write_bytes(broken)
    with pytest.raises(UnsafeEncryptedBackup):
        rotate_synthetic_archive(source, dest, OLD, NEW, main.Base.metadata)
    assert not dest.exists()


def test_v99_rotation_cli_never_echoes_passphrase(tmp_path, monkeypatch):
    source, dest, raw = fixture(tmp_path, monkeypatch)
    env = dict(os.environ)
    env['BEERSHOP_V99_ROTATION_ACK'] = ACK
    env.pop('DATABASE_URL', None)
    env.pop('BEERSHOP_PERSISTENT_DATABASE_URL', None)
    res = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(dest)],
        input=OLD + '\n' + NEW + '\n' + NEW + '\n',
        capture_output=True, text=True, env=env, cwd=ROOT,
        check=False, timeout=90)
    assert res.returncode == 0, res.stderr
    assert NEW not in res.stdout + res.stderr
    assert OLD not in res.stdout + res.stderr
    assert decrypt_archive(dest.read_bytes(), NEW) == raw
    wrong = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(dest)],
        input=OLD + '\n' + NEW + '\n' + NEW + '\n',
        capture_output=True, text=True, env=env, cwd=ROOT,
        check=False, timeout=90)
    assert wrong.returncode == 1
    assert 'Traceback' not in wrong.stderr
