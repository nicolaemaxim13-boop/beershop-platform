"""v9 staff pilot: MFA, Argon2id, replay prevention, role isolation and revocation.

All accounts and sessions are created in the GitHub Actions isolated database.
No production identities or existing customer data are touched.
"""
import base64
import re
import secrets
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as main
from app.staff_v9 import totp_at, matching_counter

STRONG = 'Testing-only-unpredictable-passphrase-92'


def _seed(uri):
    return parse_qs(urlparse(uri).query)['secret'][0]


def _code(uri):
    return totp_at(_seed(uri), int(time.time() // 30))


def _auth(token):
    return {'Authorization': 'Bearer ' + token}


@pytest.fixture
def staff_env(monkeypatch):
    monkeypatch.setenv('BEERSHOP_V9_STAFF_AUTH', '1')
    monkeypatch.setenv('BEERSHOP_V9_MFA_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    monkeypatch.delenv('BEERSHOP_V9_AUDIT_TOKEN', raising=False)
    return monkeypatch


def test_totp_single_use_and_clock_skew():
    seed = base64.b32encode(b'12345678901234567890').decode().rstrip('=')
    now = 1700000000
    step = now // 30
    code = totp_at(seed, step)
    assert matching_counter(seed, code, now=now) == step
    assert matching_counter(seed, code, now=now, after=step) is None
    assert matching_counter(seed, totp_at(seed, step-1), now=now) == step-1
    assert matching_counter(seed, totp_at(seed, step+1), now=now) == step+1
    assert matching_counter(seed, 'ABCDEF', now=now) is None


def test_staff_disabled_by_default(monkeypatch):
    monkeypatch.delenv('BEERSHOP_V9_STAFF_AUTH', raising=False)
    with TestClient(main.app) as client:
        assert client.post('/api/admin/v9/staff/login', json={
            'username':'nominal', 'password':STRONG,'otp':'000000'}).status_code == 503
        assert client.get('/api/admin/v9/staff/users').status_code == 503


def test_roles_and_encrypted_seed_and_revocation(staff_env):
    suffix = uuid4().hex[:12]
    owner_name, catalog_name = 'own'+suffix, 'cat'+suffix
    with TestClient(main.app) as client:
        with main.SessionLocal() as db:
            owner, owner_uri = main.create_staff_user_v9(db,owner_name,STRONG,'owner')
            user_id = owner.id
            assert owner.password_hash != STRONG
            assert owner.password_hash.startswith('$argon2id$')
            assert _seed(owner_uri) not in owner.mfa_ciphertext
            assert owner.mfa_ciphertext.startswith('gAAAAA')
        login = client.post('/api/admin/v9/staff/login', json={
            'username': owner_name, 'password': STRONG, 'otp': _code(owner_uri)})
        assert login.status_code == 200, login.text
        result=login.json()
        assert result['role']=='owner' and result['expires_in']==900
        owner_token=result['access_token']
        assert len(owner_token)>32
        assert owner_token not in str(main.StaffSessionV9.__table__)
        with main.SessionLocal() as db:
            session = db.scalar(select(main.StaffSessionV9).where(
                main.StaffSessionV9.user_id == user_id))
            assert session.token_hash != owner_token
            assert re.fullmatch(r'[0-9a-f]{64}',session.token_hash)
        # A successfully used TOTP code must never authorize a second login.
        replay=client.post('/api/admin/v9/staff/login', json={
            'username': owner_name,'password':STRONG,'otp':_code(owner_uri)})
        assert replay.status_code == 401
        owner_headers=_auth(owner_token)
        assert client.get('/api/admin/v9/staff/me',headers=owner_headers).json()['role']=='owner'
        assert client.get('/api/admin/v9/staff/readiness',headers=owner_headers).status_code==200
        assert client.get('/api/admin/v9/staff/catalog-summary',headers=owner_headers).status_code==200
        assert client.get('/api/admin/v9/staff/order-counts',headers=owner_headers).status_code==200
        assert client.get('/api/admin/v9/staff/users',headers=owner_headers).status_code==200
        # This session is NEVER valid for legacy full-admin actions, PII exports or payments.
        for legacy in ('/api/admin/orders', '/api/admin/maintenance/export',
                       '/api/admin/pricing/policy', '/api/admin/crm/orders'):
            assert client.get(legacy,headers=owner_headers).status_code==401,legacy

        new=client.post('/api/admin/v9/staff/users',headers=owner_headers,json={
            'username':catalog_name,'password':STRONG+'-new','role':'catalog'})
        assert new.status_code==201,new.text
        catalog_uri=new.json()['mfa_enrollment_uri']
        assert _seed(catalog_uri) not in str(client.get(
            '/api/admin/v9/staff/users',headers=owner_headers).json())
        wrong=client.post('/api/admin/v9/staff/login',json={
            'username': catalog_name, 'password':'wrong','otp':_code(catalog_uri)})
        assert wrong.status_code==401
        logged=client.post('/api/admin/v9/staff/login',json={
            'username':catalog_name,'password':STRONG+'-new','otp':_code(catalog_uri)})
        assert logged.status_code==200,logged.text
        catalog_headers=_auth(logged.json()['access_token'])
        assert client.get('/api/admin/v9/staff/catalog-summary',headers=catalog_headers).status_code==200
        assert client.get('/api/admin/v9/staff/readiness',headers=catalog_headers).status_code==403
        assert client.get('/api/admin/v9/staff/order-counts',headers=catalog_headers).status_code==403
        assert client.get('/api/admin/v9/staff/users',headers=catalog_headers).status_code==403
        assert client.post('/api/admin/v9/staff/users',headers=catalog_headers,json={
            'username':'badrole','password':STRONG,'role':'owner'}).status_code==403
        assert client.post('/api/admin/v9/staff/users/'+str(user_id)+'/disable',
                           headers=owner_headers).status_code==409
        listed=client.get('/api/admin/v9/staff/users',headers=owner_headers).json()
        catid=next(u['id'] for u in listed if u['username']==catalog_name)
        disabled=client.post('/api/admin/v9/staff/users/'+str(catid)+'/disable',
                             headers=owner_headers)
        assert disabled.status_code==200
        assert disabled.json()['sessions_revoked']>=1
        assert client.get('/api/admin/v9/staff/me',headers=catalog_headers).status_code==401
        assert client.post('/api/admin/v9/staff/logout',headers=owner_headers).status_code==200
        assert client.get('/api/admin/v9/staff/me',headers=owner_headers).status_code==401


def test_auth_lockout_and_key_rotation_fail_closed(staff_env):
    name='lock'+uuid4().hex[:10]
    with TestClient(main.app) as client:
        with main.SessionLocal() as db:
            user,uri=main.create_staff_user_v9(db,name,STRONG,'auditor')
        valid=_code(uri)
        wrong=f'{(int(valid)+1) % 1_000_000:06d}'
        for _ in range(5):
            fail=client.post('/api/admin/v9/staff/login',json={
                'username':name,'password':STRONG,'otp':wrong})
            assert fail.status_code==401
        assert client.post('/api/admin/v9/staff/login',json={
            'username':name,'password':STRONG,'otp':valid}).status_code==401
        with main.SessionLocal() as db:
            account=db.get(main.StaffUserV9,user.id)
            assert account.locked_until > time.time()
            account.locked_until=0
            db.commit()
        staff_env.setenv('BEERSHOP_V9_MFA_KEY',Fernet.generate_key().decode())
        res=client.post('/api/admin/v9/staff/login',json={
            'username':name,'password':STRONG,'otp':valid})
        assert res.status_code==503
        assert 'secret' not in res.text.lower()



def test_staff_ui_disabled_by_default_and_secure_when_enabled(staff_env):
    staff_env.delenv('BEERSHOP_V9_STAFF_AUTH', raising=False)
    with TestClient(main.app) as c:
        assert c.get('/staff-v9').status_code == 503
        assert c.get('/assets/staff-v9.js').status_code == 503
        assert c.get('/assets/staff-v9.css').status_code == 503
    staff_env.setenv('BEERSHOP_V9_STAFF_AUTH', '1')
    with TestClient(main.app) as c:
        page=c.get('/staff-v9')
        assert page.status_code == 200
        assert 'Персональний вхід співробітника' in page.text
        assert page.headers['cache-control'] == 'private, no-store'
        assert page.headers['x-frame-options'] == 'DENY'
        assert "default-src 'none'" in page.headers['content-security-policy']
        assert "script-src 'self'" in page.headers['content-security-policy']
        assert "frame-ancestors 'none'" in page.headers['content-security-policy']
        for asset,media in (('staff-v9.js','javascript'),('staff-v9.css','css')):
            result=c.get('/assets/'+asset)
            assert result.status_code == 200
            assert media in result.headers['content-type']
            assert result.headers['cache-control'] == 'no-store'
        assert 'localStorage' not in c.get('/assets/staff-v9.js').text
