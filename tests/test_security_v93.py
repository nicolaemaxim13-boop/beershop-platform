"""v9.3 owner step-up MFA recovery and bounded audit retention.

Strictly synthetic test identities and ephemeral SQLite/PostgreSQL CI DB.
No emails, customer PII, live credentials, real orders or bank calls.
"""
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, func

import app.main as main
import app.staff_v9 as staff


PASSWORD = 'Strictly-Synthetic-Owner-Password-2026'
STAFF_PASSWORD = 'Strictly-Synthetic-Staff-Password-2026'


def seed(uri):
    return parse_qs(urlparse(uri).query)['secret'][0]


def otp(uri, now):
    return staff.totp_at(seed(uri), now // 30)


def headers(token):
    return {'Authorization': 'Bearer ' + token}


@pytest.fixture
def v93_env(monkeypatch):
    monkeypatch.setenv('BEERSHOP_V9_STAFF_AUTH', '1')
    monkeypatch.setenv('BEERSHOP_V9_MFA_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    monkeypatch.delenv('BEERSHOP_V91_ENFORCE_RBAC', raising=False)
    return monkeypatch


def create(db, role, password):
    name = ('rec'+role[:3]+uuid4().hex[:9])[:32]
    user, uri = main.create_staff_user_v9(db, name, password, role)
    return user, uri


def login(c, user, uri, password, now):
    result = c.post('/api/admin/v9/staff/login', json={
        'username': user.username, 'password': password, 'otp': otp(uri, now)})
    assert result.status_code == 200, result.text
    return headers(result.json()['access_token'])


def test_owner_recovery_blocks_old_factor_and_revokes_sessions(v93_env, monkeypatch):
    clock = [1_900_000_020]
    monkeypatch.setattr(staff.time, 'time', lambda: clock[0])
    with TestClient(main.app) as c:
        with main.SessionLocal() as db:
            owner, owner_uri = create(db, 'owner', PASSWORD)
            operator, old_uri = create(db, 'operations', STAFF_PASSWORD)
        owner_headers = login(c, owner, owner_uri, PASSWORD, clock[0])
        old_headers = login(c, operator, old_uri, STAFF_PASSWORD, clock[0])
        assert c.get('/api/admin/v9/staff/me', headers=old_headers).status_code == 200
        clock[0] += 31
        request = {
            'owner_password': PASSWORD, 'owner_otp': otp(owner_uri, clock[0]),
            'confirm_username': operator.username,
        }
        endpoint = f'/api/admin/v9/staff/users/{operator.id}/mfa-recovery'
        # A stolen owner session without owner password cannot reset MFA.
        bad = c.post(endpoint, headers=owner_headers, json={
            **request, 'owner_password': 'not-the-owner-password'})
        assert bad.status_code == 403
        wrong_target = c.post(endpoint, headers=owner_headers, json={
            **request, 'confirm_username': 'unknownsynthetic'})
        assert wrong_target.status_code == 409
        reset = c.post(endpoint, headers=owner_headers, json=request)
        assert reset.status_code == 200, reset.text
        data = reset.json()
        assert data['username'] == operator.username
        assert data['sessions_revoked'] >= 1
        assert data['expires_in'] == 1800
        new_uri = data['mfa_enrollment_uri']
        assert seed(new_uri) != seed(old_uri)
        assert c.get('/api/admin/v9/staff/me', headers=old_headers).status_code == 401
        # Fresh owner MFA code is consumed; another reset cannot replay it.
        assert c.post(endpoint, headers=owner_headers, json=request).status_code == 403
        # Even if an old factor is valid, recovery-pending requires NEW factor.
        old = c.post('/api/admin/v9/staff/login', json={
            'username': operator.username, 'password': STAFF_PASSWORD,
            'otp': otp(old_uri, clock[0])})
        assert old.status_code == 401
        new_headers = login(c, operator, new_uri, STAFF_PASSWORD, clock[0])
        assert c.get('/api/admin/v9/staff/me', headers=new_headers).status_code == 200
        assert c.post('/api/admin/v9/staff/login', json={
            'username': operator.username, 'password': STAFF_PASSWORD,
            'otp': otp(old_uri, clock[0])}).status_code == 401
        assert c.post('/api/admin/v9/staff/login', json={
            'username': operator.username, 'password': STAFF_PASSWORD,
            'otp': otp(new_uri, clock[0])}).status_code == 401
        users = c.get('/api/admin/v9/staff/users', headers=owner_headers)
        assert users.status_code == 200
        assert seed(new_uri) not in users.text
        events = c.get('/api/admin/v9/staff/security-events', headers=owner_headers)
        assert events.status_code == 200
        assert 'mfa_recovery_started' in events.text
        assert 'mfa_recovery_completed' in events.text
        assert seed(new_uri) not in events.text
        assert PASSWORD not in events.text


def test_recovery_never_resets_owner_and_expires_closed(v93_env, monkeypatch):
    clock = [1_900_100_020]
    monkeypatch.setattr(staff.time, 'time', lambda: clock[0])
    with TestClient(main.app) as c:
        with main.SessionLocal() as db:
            owner, owner_uri = create(db, 'owner', PASSWORD)
            other_owner, other_uri = create(db, 'owner', PASSWORD+'-different')
            colleague, old_uri = create(db, 'catalog', STAFF_PASSWORD)
        owner_headers = login(c, owner, owner_uri, PASSWORD, clock[0])
        clock[0] += 31
        payload = {
            'owner_password': PASSWORD, 'owner_otp': otp(owner_uri, clock[0]),
            'confirm_username': other_owner.username,
        }
        self_or_owner = c.post(
            f'/api/admin/v9/staff/users/{other_owner.id}/mfa-recovery',
            headers=owner_headers, json=payload)
        assert self_or_owner.status_code == 409
        # Rejected owner target should not consume the step-up code.
        payload['confirm_username'] = colleague.username
        accepted = c.post(f'/api/admin/v9/staff/users/{colleague.id}/mfa-recovery',
                          headers=owner_headers, json=payload)
        assert accepted.status_code == 200
        pending_uri = accepted.json()['mfa_enrollment_uri']
        clock[0] += 1801
        for uri in (old_uri, pending_uri):
            expired = c.post('/api/admin/v9/staff/login', json={
                'username': colleague.username, 'password': STAFF_PASSWORD,
                'otp': otp(uri, clock[0])})
            assert expired.status_code == 401
        # No online backdoor to recover an owner account.
        assert c.get('/api/admin/v9/staff/users', headers=owner_headers).status_code == 401


def test_audit_retention_requires_stepup_and_limits_deletion(v93_env, monkeypatch):
    clock = [1_900_200_020]
    monkeypatch.setattr(staff.time, 'time', lambda: clock[0])
    with TestClient(main.app) as c:
        with main.SessionLocal() as db:
            owner, owner_uri = create(db, 'owner', PASSWORD)
            audit_user, audit_uri = create(db, 'auditor', STAFF_PASSWORD)
        owner_headers = login(c, owner, owner_uri, PASSWORD, clock[0])
        auditor_headers = login(c, audit_user, audit_uri, STAFF_PASSWORD, clock[0])
        path = '/api/admin/v9/staff/security-events/retention'
        assert c.get(path, headers=auditor_headers).status_code == 403
        table = main.Base.metadata.tables['v9_staff_security_events']
        with main.SessionLocal.begin() as db:
            old_at = clock[0] - 91 * 86400
            db.execute(insert(table), [
                {'event_type': 'login_failed', 'subject_hash': 'a'*64,
                 'occurred_at': old_at, 'actor_user_id': None} for _ in range(503)])
        preview = c.get(path, headers=owner_headers)
        assert preview.status_code == 200
        assert preview.json()['eligible_count'] >= 503
        assert preview.json()['auto_prune_enabled'] is False
        prune_path = '/api/admin/v9/staff/security-events/prune'
        assert c.post(prune_path, headers=auditor_headers, json={}).status_code == 403
        clock[0] += 31
        payload = {
            'owner_password': PASSWORD, 'owner_otp': otp(owner_uri, clock[0]),
            'confirmation':'PRUNE_STAGING_EVENTS_OLDER_THAN_90_DAYS'
        }
        assert c.post(prune_path, headers=owner_headers, json={
            **payload, 'confirmation':'NO'}).status_code == 422
        assert c.post(prune_path, headers=owner_headers, json={
            **payload, 'owner_password':'bad'}).status_code == 403
        first = c.post(prune_path, headers=owner_headers, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()['deleted_old_events'] == 500
        assert c.get(path, headers=owner_headers).json()['eligible_count'] >= 3
        assert c.post(prune_path, headers=owner_headers, json=payload).status_code == 403
        with main.SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(table).where(
                table.c.event_type == 'security_retention_prune')) == 1
