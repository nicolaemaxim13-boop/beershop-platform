"""BeerShop v9.4 synthetic-only encrypted archive and PostgreSQL rehearsal.

Runs without touching live customer records, real financial data or Neon.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, func, text

import app.main as main
from app.backup_v9 import add_integrity, verify_snapshot
from app.secure_backup_v94 import (
    encrypt_archive, decrypt_archive, write_private_exclusive,
    UnsafeEncryptedBackup, MAGIC)
from app.pg_rehearsal_v94 import (
    validate_local_rehearsal_target, rehearsal_restore_empty_postgres,
    REHEARSAL_DB_NAME)
from app.schema_audit_v94 import schema_inventory

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts' / 'secure_backup_v94.py'
PASSWORD = 'synthetic-private-passphrase-at-least-20-characters'


def snapshot_bytes():
    engine = create_engine('sqlite:///:memory:')
    main.Base.metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(main.Store.__table__.insert(), {
            'id':1, 'slug':'beershop', 'name':'PRIVATE_SYNTHETIC_FIXTURE'})
        db.execute(main.Category.__table__.insert(), {
            'id':1, 'store_id':1, 'slug':'fake-test', 'name':'Synthetic'})
        tables = {t.name:[dict(r._mapping) for r in db.execute(select(t)).all()]
                  for t in main.Base.metadata.sorted_tables}
    engine.dispose()
    payload = add_integrity({
        'format':'beershop-snapshot-v1',
        'created_at':'2026-11-12T12:00:00+00:00',
        'tables': tables,
    }, main.Base.metadata)
    return json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')


def test_aes_gcm_roundtrip_is_private_and_authenticated(tmp_path):
    raw = snapshot_bytes()
    encrypted = encrypt_archive(raw, PASSWORD)
    assert encrypted.startswith(MAGIC)
    assert b'PRIVATE_SYNTHETIC_FIXTURE' not in encrypted
    assert raw != encrypted
    assert decrypt_archive(encrypted, PASSWORD) == raw
    assert encrypt_archive(raw, PASSWORD) != encrypted  # randomized salt + nonce
    with pytest.raises(UnsafeEncryptedBackup):
        decrypt_archive(encrypted, 'incorrect-long-synthetic-password-0001')
    tampered = bytearray(encrypted)
    tampered[-1] ^= 1
    with pytest.raises(UnsafeEncryptedBackup):
        decrypt_archive(bytes(tampered), PASSWORD)
    with pytest.raises(UnsafeEncryptedBackup):
        encrypt_archive(raw, 'too-short')
    with pytest.raises(UnsafeEncryptedBackup):
        decrypt_archive(b'garbage', PASSWORD)

    destination = tmp_path / 'private.bse'
    write_private_exclusive(destination, encrypted)
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.read_bytes() == encrypted
    with pytest.raises(FileExistsError):
        write_private_exclusive(destination, encrypted)
    assert destination.read_bytes() == encrypted
    shortcut = tmp_path / 'symlink.bse'
    shortcut.symlink_to(destination)
    with pytest.raises(FileExistsError):
        write_private_exclusive(shortcut, encrypted)
    assert destination.read_bytes() == encrypted


def cmd(*args, input=''):
    env = dict(os.environ)
    env.pop('BEERSHOP_PERSISTENT_DATABASE_URL', None)
    env.pop('DATABASE_URL', None)
    return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                          input=input, capture_output=True, text=True,
                          cwd=ROOT, env=env, timeout=90, check=False)


def test_offline_encrypted_cli_verify_and_local_rehearsal(tmp_path):
    source = tmp_path/'synthetic-private.json'
    source.write_bytes(snapshot_bytes())
    encrypted = tmp_path/'encrypted.bse'
    result = cmd('encrypt', source, encrypted, input=PASSWORD+'\n'+PASSWORD+'\n')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['encrypted'] is True
    assert encrypted.exists()
    assert encrypted.stat().st_mode & 0o777 == 0o600
    assert source.exists()  # no automatic, surprising deletion of source
    assert PASSWORD not in result.stdout + result.stderr
    success = cmd('verify', encrypted, '--rehearse', input=PASSWORD+'\n')
    assert success.returncode == 0, success.stderr
    data = json.loads(success.stdout)
    assert data['verified'] and data['local_sqlite_restore']
    assert PASSWORD not in success.stdout + success.stderr
    wrong = cmd('verify', encrypted, input='wrong-synthetic-password-very-long\n')
    assert wrong.returncode == 1
    assert not wrong.stdout
    assert 'Traceback' not in wrong.stderr
    # No plaintext PII is ever written to a second file by verification.
    assert list(tmp_path.glob('*.db')) == []
    # Existing target must remain untouched.
    assert cmd('encrypt', source, encrypted,
               input=PASSWORD+'\n'+PASSWORD+'\n').returncode == 1
    assert decrypt_archive(encrypted.read_bytes(), PASSWORD) == source.read_bytes()


def test_schema_inventory_is_read_only_and_reports_drift():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        report = schema_inventory(conn, main.Base.metadata)
        assert report['read_only'] is True
        assert report['missing_tables']
        assert not report['schema_matches_columns']
        main.Base.metadata.create_all(conn)
        ok = schema_inventory(conn, main.Base.metadata)
        assert ok['schema_matches_columns'] is True
        conn.execute(text('CREATE TABLE audit_fixture_extra (id INTEGER)'))
        bad = schema_inventory(conn, main.Base.metadata)
        assert bad['extra_tables'] == ['audit_fixture_extra']
    engine.dispose()


def test_pg_rehearsal_target_is_restricted(monkeypatch):
    from app.backup_v9 import UnsafeBackup
    monkeypatch.delenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL', raising=False)
    with pytest.raises(UnsafeBackup):
        validate_local_rehearsal_target(
            'postgresql://example:fake@localhost/beershop_v94_rehearsal')
    monkeypatch.setenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL','1')
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    for url in (
        'postgresql://example:fake@ep-neon.neon.tech/beershop_v94_rehearsal',
        'postgresql://example:fake@localhost/beershop_test',
        'sqlite:///./beershop_v94_rehearsal.db',
    ):
        with pytest.raises(UnsafeBackup):
            validate_local_rehearsal_target(url)
    allowed = validate_local_rehearsal_target(
        'postgresql://test:fake@127.0.0.1:5432/beershop_v94_rehearsal')
    assert allowed.database == REHEARSAL_DB_NAME
    monkeypatch.setenv('BEERSHOP_PERSISTENT_DATABASE_URL','unsafe-should-block')
    with pytest.raises(UnsafeBackup):
        validate_local_rehearsal_target(
            'postgresql://test:fake@localhost/beershop_v94_rehearsal')


def test_postgres_restore_only_in_ephemeral_ci_database(monkeypatch):
    from app.backup_v9 import UnsafeBackup
    url = os.getenv('BEERSHOP_V94_PG_REHEARSAL_URL')
    if not url:
        pytest.skip('Only PostgreSQL CI service provides a separate empty rehearsal database')
    monkeypatch.setenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL','1')
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL',raising=False)
    raw = snapshot_bytes()
    checks = verify_snapshot(raw,main.Base.metadata)
    result = rehearsal_restore_empty_postgres(raw, main.Base.metadata, url)
    assert result['mode'] == 'ISOLATED_PG_REHEARSAL_ONLY'
    assert result['rows'] == checks['total_rows']
    engine = create_engine(url)
    with engine.connect() as db:
        assert int(db.scalar(select(func.count()).select_from(main.Store))) == 1
        assert db.scalar(select(main.Store.name)) == 'PRIVATE_SYNTHETIC_FIXTURE'
        assert schema_inventory(db, main.Base.metadata)['schema_matches_columns']
    engine.dispose()
    # Never touch an already initialized database again.
    with pytest.raises(UnsafeBackup, match='no tables'):
        rehearsal_restore_empty_postgres(raw, main.Base.metadata, url)
