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
    # Compare every table, including empty ones, against the authenticated
    # manifest; checking only the store/category sample misses partial restores.
    from app.secure_backup_v94 import decrypt_archive
    from app.backup_v9 import _schema_id
    manifest = json.loads(decrypt_archive(archive, PASS))['integrity']
    assert manifest['schema_sha256'] == _schema_id(main.Base.metadata)
    assert report['tables'] == len(main.Base.metadata.tables)
    assert report['rows'] == sum(manifest['counts'].values())
    assert PASS not in str(report)
    assert 'postgresql://' not in str(report)

    engine = create_engine(sqlalchemy_url(url))
    with engine.connect() as db:
        assert db.scalar(select(main.Store.name)) == 'SYNTHETIC_ENCRYPTED_V96'
        assert db.scalar(select(func.count()).select_from(main.Category)) == 1
        for table in main.Base.metadata.sorted_tables:
            actual = db.scalar(select(func.count()).select_from(table))
            assert actual == manifest['counts'][table.name], table.name
        inspector = __import__('sqlalchemy').inspect(db)
        assert set(inspector.get_table_names()) == set(manifest['counts'])
        _assert_foreign_key_contract(main.Base.metadata, inspector)
    engine.dispose()
    with pytest.raises(UnsafeBackup, match='no tables'):
        rehearsal_restore_encrypted_postgres(
            archive, PASS, main.Base.metadata, url)


def test_v101_pg_authenticated_invalid_manifest_fails_before_connect(monkeypatch):
    """Valid AES-GCM is insufficient: manifest integrity must pass before PG access."""
    monkeypatch.setenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL', '1')
    monkeypatch.setenv('BEERSHOP_V96_ENCRYPTED_RESTORE_ACK',
                       'ISOLATED_ENCRYPTED_RESTORE_ONLY')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    from app.secure_backup_v94 import decrypt_archive
    payload = json.loads(decrypt_archive(synthetic_encrypted(), PASS))
    payload['integrity']['schema_sha256'] = 'f' * 64
    encrypted = encrypt_archive(json.dumps(payload).encode(), PASS)
    # No PG server is needed: failure must happen before any connection attempt.
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_encrypted_postgres(
            encrypted, PASS, main.Base.metadata,
            'postgresql://fake:fake@localhost/beershop_v96_encrypted_restore')


def test_v101_pg_authenticated_missing_table_fails_before_connect(monkeypatch):
    """Authenticated payload lacking a required table cannot initiate restore."""
    monkeypatch.setenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL', '1')
    monkeypatch.setenv('BEERSHOP_V96_ENCRYPTED_RESTORE_ACK',
                       'ISOLATED_ENCRYPTED_RESTORE_ONLY')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    from app.secure_backup_v94 import decrypt_archive
    payload = json.loads(decrypt_archive(synthetic_encrypted(), PASS))
    payload['tables'].pop('stores')
    encrypted = encrypt_archive(json.dumps(payload).encode(), PASS)
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_encrypted_postgres(
            encrypted, PASS, main.Base.metadata,
            'postgresql://fake:fake@localhost/beershop_v96_encrypted_restore')


def test_v101_pg_authenticated_row_tampering_fails_before_connect(monkeypatch):
    """Re-encrypting a modified row must not bypass snapshot integrity checks."""
    monkeypatch.setenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL', '1')
    monkeypatch.setenv('BEERSHOP_V96_ENCRYPTED_RESTORE_ACK',
                       'ISOLATED_ENCRYPTED_RESTORE_ONLY')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    from app.secure_backup_v94 import decrypt_archive
    payload = json.loads(decrypt_archive(synthetic_encrypted(), PASS))
    payload['tables']['stores'][0]['name'] = 'TAMPERED_SYNTHETIC_STORE'
    encrypted = encrypt_archive(json.dumps(payload).encode(), PASS)
    with pytest.raises(UnsafeBackup):
        rehearsal_restore_encrypted_postgres(
            encrypted, PASS, main.Base.metadata,
            'postgresql://fake:fake@localhost/beershop_v96_encrypted_restore')


def test_v101_pg_authenticated_count_manifest_mismatch_fails_before_connect(monkeypatch):
    """A valid encrypted archive with falsified per-table counts is rejected."""
    monkeypatch.setenv('BEERSHOP_V94_LOCAL_PG_REHEARSAL', '1')
    monkeypatch.setenv('BEERSHOP_V96_ENCRYPTED_RESTORE_ACK',
                       'ISOLATED_ENCRYPTED_RESTORE_ONLY')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('BEERSHOP_PERSISTENT_DATABASE_URL', raising=False)
    from app.secure_backup_v94 import decrypt_archive
    payload = json.loads(decrypt_archive(synthetic_encrypted(), PASS))
    payload['integrity']['counts']['stores'] += 1
    encrypted = encrypt_archive(json.dumps(payload).encode(), PASS)
    # No server at this address: failure must occur during manifest validation.
    with pytest.raises(UnsafeBackup, match='row counts'):
        rehearsal_restore_encrypted_postgres(
            encrypted, PASS, main.Base.metadata,
            'postgresql://fake:fake@localhost/beershop_v96_encrypted_restore')


def _assert_foreign_key_contract(metadata, inspector):
    """Compare restored foreign-key definitions, not only table/row counts."""
    for table in metadata.sorted_tables:
        expected = {
            (tuple(fk.column_keys), fk.referred_table.name,
             tuple(element.column.name for element in fk.elements))
            for fk in table.foreign_key_constraints
        }
        actual = {
            (tuple(fk['constrained_columns']), fk['referred_table'],
             tuple(fk['referred_columns']))
            for fk in inspector.get_foreign_keys(table.name)
        }
        assert actual == expected, f'Foreign-key schema drift in {table.name}'


def test_v101_foreign_key_contract_rejects_missing_constraint():
    """A matching table list and row counts cannot conceal missing FK DDL."""
    class MissingForeignKeys:
        def get_foreign_keys(self, table_name):
            return []

    with pytest.raises(AssertionError, match='Foreign-key schema drift'):
        _assert_foreign_key_contract(main.Base.metadata, MissingForeignKeys())


def test_v101_foreign_key_contract_rejects_unexpected_constraint():
    """A malicious or drifted extra FK must fail even when required FKs exist."""
    class ExtraForeignKeyInspector:
        def get_foreign_keys(self, table_name):
            table = main.Base.metadata.tables[table_name]
            expected = [
                {'constrained_columns': list(fk.column_keys),
                 'referred_table': fk.referred_table.name,
                 'referred_columns': [element.column.name for element in fk.elements]}
                for fk in table.foreign_key_constraints
            ]
            if table_name == 'stores':
                expected.append({'constrained_columns': ['id'],
                                 'referred_table': 'categories',
                                 'referred_columns': ['id']})
            return expected

    with pytest.raises(AssertionError, match='Foreign-key schema drift in stores'):
        _assert_foreign_key_contract(main.Base.metadata, ExtraForeignKeyInspector())
