"""v9.8 HTTP encrypted export is strictly owner-MFA-only on synthetic DBs."""
import json
import os
from uuid import uuid4
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
import app.staff_v9 as staff
from app.backup_v9 import verify_snapshot
from app.encrypted_export_v98 import (
    ACK, require_synthetic_encrypted_export,
    encrypted_snapshot_from_session)
from app.secure_backup_v94 import (
    MAGIC, decrypt_archive, UnsafeEncryptedBackup)


OWNER_PW = 'Synthetic-v98-owner-password-only-2026'
WORKER_PW = 'Synthetic-v98-worker-password-only-2026'
EXPORT_KEY = 'Synthetic-only-v98-encryption-key-not-for-production-2026'
ENDPOINT = '/api/admin/v98/maintenance/export-encrypted'
LEGACY = '/api/admin/maintenance/export'


def setup_env(monkeypatch):
    monkeypatch.setenv('BEERSHOP_V98_ENCRYPTED_EXPORT', '1')
    monkeypatch.setenv('BEERSHOP_V98_EXPORT_ACK', ACK)
    monkeypatch.setenv('BEERSHOP_V98_EXPORT_PASSPHRASE', EXPORT_KEY)
    monkeypatch.setenv('BEERSHOP_V91_STAGING_ACK', 'ISOLATED_STAGING_ONLY')
    monkeypatch.setenv('BEERSHOP_V91_ENFORCE_RBAC', '1')
    monkeypatch.setenv('BEERSHOP_V9_STAFF_AUTH', '1')
    monkeypatch.setenv('BEERSHOP_V9_MFA_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    monkeypatch.delenv('BEERSHOP_V95_RATE_LIMIT', raising=False)
    monkeypatch.delenv('BEERSHOP_V96_EDGE_SIGNED_CLIENT', raising=False)


def _login(client, user, uri, password):
    totp_seed = parse_qs(urlparse(uri).query)['secret'][0]
    code = staff.totp_at(totp_seed, int(staff.time.time()) // 30)
    response = client.post('/api/admin/v9/staff/login', json={
        'username': user.username, 'password': password, 'otp': code})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}


def test_v98_encrypted_export_owner_mfa_only_and_ciphertext_only(monkeypatch):
    setup_env(monkeypatch)
    with TestClient(main.app) as client:
        with main.SessionLocal() as db:
            owner, uri = main.create_staff_user_v9(
                db, 'v98owner'+uuid4().hex[:10], OWNER_PW, 'owner')
            worker, worker_uri = main.create_staff_user_v9(
                db, 'v98worker'+uuid4().hex[:10], WORKER_PW, 'catalog')
        owner_auth = _login(client, owner, uri, OWNER_PW)
        worker_auth = _login(client, worker, worker_uri, WORKER_PW)

        assert client.get(ENDPOINT).status_code == 401
        assert client.get(ENDPOINT, headers={
            'Authorization':'Bearer ' + 't'*40}).status_code == 401
        assert client.get(ENDPOINT, headers=worker_auth).status_code == 403
        assert client.get(LEGACY, headers=worker_auth).status_code == 403
        assert client.get(LEGACY, headers=owner_auth).status_code == 403
        response = client.get(ENDPOINT, headers=owner_auth)
        assert response.status_code == 200, response.text[:300] if response.status_code != 200 else ''
        assert response.content.startswith(MAGIC)
        assert response.headers['content-type'].startswith('application/octet-stream')
        assert 'no-store' in response.headers['cache-control']
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert 'beershop-staging-private-v98.bse' in response.headers['content-disposition']
        assert b'BeerShop Equipment' not in response.content
        assert OWNER_PW.encode() not in response.content
        assert EXPORT_KEY.encode() not in response.content
        plaintext = decrypt_archive(response.content, EXPORT_KEY)
        assert b'BeerShop Equipment' in plaintext
        report = verify_snapshot(plaintext, main.Base.metadata)
        assert report['ok'] is True
        assert report['counts']['stores'] >= 1
        tamper = bytearray(response.content)
        tamper[-1] ^= 0x80
        with pytest.raises(UnsafeEncryptedBackup):
            decrypt_archive(bytes(tamper), EXPORT_KEY)
        assert worker.username not in response.text


def test_v98_export_refuses_disabled_or_bad_config_before_db_read(monkeypatch):
    setup_env(monkeypatch)
    valid_database = main.DB_URL
    valid_persistent = main.PERSISTENT_URL
    # Test the guard directly; it does not connect to a DB and cannot write.
    assert require_synthetic_encrypted_export(
        database_url=valid_database, persistent_url=valid_persistent) == EXPORT_KEY
    for database_url, persistent_url in (
        ('postgresql://fake:fake@ep-neon.neon.tech/beershop_test', ''),
        ('postgresql://fake:fake@localhost:5432/customers', ''),
        ('sqlite:///./real-customers.db', ''),
        (valid_database, 'postgresql://fake:fake@neon.tech/live'),
    ):
        with pytest.raises(HTTPException) as error:
            require_synthetic_encrypted_export(
                database_url=database_url, persistent_url=persistent_url)
        assert error.value.status_code == 503

    monkeypatch.setenv('BEERSHOP_V98_EXPORT_PASSPHRASE', 'short')
    with pytest.raises(HTTPException):
        require_synthetic_encrypted_export(database_url=valid_database,
                                           persistent_url=valid_persistent)
    monkeypatch.setenv('BEERSHOP_V98_EXPORT_PASSPHRASE', EXPORT_KEY)
    monkeypatch.delenv('BEERSHOP_V98_EXPORT_ACK', raising=False)
    with pytest.raises(HTTPException):
        require_synthetic_encrypted_export(database_url=valid_database,
                                           persistent_url=valid_persistent)
    monkeypatch.setenv('BEERSHOP_V98_EXPORT_ACK', ACK)
    monkeypatch.setenv('BEERSHOP_V91_ENFORCE_RBAC', '0')
    with pytest.raises(HTTPException):
        require_synthetic_encrypted_export(database_url=valid_database,
                                           persistent_url=valid_persistent)
    monkeypatch.setenv('BEERSHOP_V91_ENFORCE_RBAC', '1')
    monkeypatch.setenv('BEERSHOP_V98_ENCRYPTED_EXPORT', '0')
    with TestClient(main.app) as client:
        # Production-equivalent legacy mode has not been changed.
        assert client.get(ENDPOINT).status_code == 503
        assert client.get(LEGACY).status_code == 401
