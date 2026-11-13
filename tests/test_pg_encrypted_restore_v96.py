"""v9.6: decrypt only in memory; restore only into empty disposable PostgreSQL."""
import os
import json

import pytest
from sqlalchemy import create_engine, select, func

from app import main
from app.backup_v9 import add_integrity, UnsafeBackup, verify_snapshot
from app.secure_backup_v94 import encrypt_archive, UnsafeEncryptedBackup
from app.pg_encrypted_restore_v96 import rehearsal_restore_encrypted_postgres
from app.pg_rehearsal_v94 import validate_local_rehearsal_target

PASS = 'Synthetic-only-v96-encrypted-PG-rehearsal-password'


def synthetic_encrypted():
    engine = create_engine('sqlite:///:memory:')
    main.Base.metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(main.Store.__table__.insert(), {
            'id': 1, 'slug': 'beershop', 'name': 'SYNTHETIC_ENCRYPTED_V96'})
        db.execute(main.Category.__table__.insert(), {
            'id': 1, 'store_id': 1, 'slug': 'fake', 'name': 'Synthetic'})
        rows = {t.name: [dict(r._mapping) for r in db.execute(select(t)).all()]
                for t in main.Base.metadata.sorted_tables}
    engine.dispose()
    payload = add_integrity({
        'format': 'beershop-snapshot-v1',
        'created_at': '2026-11-13T09:00:00+00:00',
        'tables': rows,
    }, main.Base.metadata)
    return encrypt_archive(json.dumps(payload, ensure_ascii=False, default=str).encode(),
                           PASS)


def test_v96_encrypted_restore_refuses_remote_and_missing_ack(monkeypatch):
    archive = synthetic_encrypted()
    monkeypatch.setenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL', '1')
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_V96_ENCRYPTED_RESTORE_ACK', raising=False)
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_encrypted_postgres(
            archive, PASS, main.Base.metadata,
            'postgresql://bad:fake@localhost/beershop_v96_encrypted_restore')
    monkeypatch.setenv('BEERSHOP_V96_ENCRYPTED_RESTORE_ACK',
                       'ISOLATED_ENCRYPTED_RESTORE_ONLY')
    for url in (
        'postgresql://fake:fake@ep-neon.neon.tech/beershop_v96_encrypted_restore',
        'postgresql://fake:fake@localhost/beershop_v94_rehearsal',
        'postgresql://fake:fake@localhost/beershop_test',
        'sqlite:///tmp/beershop_v96_encrypted_restore.db',
    ):
        with pytest.raises(UnsafeBackup):
            rehearsal_restore_encrypted_postgres(archive, PASS, main.Base.metadata, url)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fake:fake@localhost/beershop_v96_encrypted_restore')
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_encrypted_postgres(
            archive, PASS, main.Base.metadata,
            'postgresql://fake:fake@localhost/beershop_v96_encrypted_restore')


def test_v96_decrypt_then_restore_exactly_once_in_pg_ci(monkeypatch):
    url = os.getenv('BEERSHOP_V96_PG_ENCRYPTED_RESTORE_URL')
    if not url:
        pytest.skip('Only disposable PostgreSQL CI provides separate encrypted restore DB')
    archive = synthetic_encrypted()
    monkeypatch.setenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL', '1')
    monkeypatch.setenv('BEERSHOP_V96_ENCRYPTED_RESTORE_ACK',
                       'ISOLATED_ENCRYPTED_RESTORE_ONLY')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    with pytest.raises(UnsafeEncryptedBackup):
        rehearsal_restore_encrypted_postgres(
            archive, 'Incorrect-synthetic-password-at-least-20',
            main.Base.metadata, url)
    tampered = bytearray(archive)
    tampered[-1] ^= 1
    with pytest.raises(UnsafeEncryptedBackup):
        rehearsal_restore_encrypted_postgres(
            bytes(tampered), PASS, main.Base.metadata, url)
    from app.storage import sqlalchemy_url
    engine = create_engine(sqlalchemy_url(url))
    with engine.connect() as db:
        assert not __import__('sqlalchemy').inspect(db).get_table_names()
    engine.dispose()

    report = rehearsal_restore_encrypted_postgres(
        archive, PASS, main.Base.metadata, url)
    assert report['mode'] == 'ENCRYPTED_LOCAL_PG_REHEARSAL_ONLY'
    assert report['authenticated_encryption'] == 'AES-256-GCM'
    assert report['rows'] >= 2
    assert PASS not in str(report)
    assert 'postgresql://' not in str(report)

    engine = create_engine(sqlalchemy_url(url))
    with engine.connect() as db:
        assert db.scalar(select(main.Store.name)) == 'SYNTHETIC_ENCRYPTED_V96'
        assert db.scalar(select(func.count()).select_from(main.Category)) == 1
    engine.dispose()
    with pytest.raises(UnsafeBackup, match='no tables'):
        rehearsal_restore_encrypted_postgres(
            archive, PASS, main.Base.metadata, url)
