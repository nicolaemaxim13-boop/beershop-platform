"""v9.1 strict staging RBAC: complete route map, MFA sessions, backup and PII.

All operations use isolated CI SQLite/Postgres synthetic data; external provider
requests must be refused even if fake keys are accidentally configured.
"""
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main
from app.backup_v9 import verify_snapshot, rehearsal_restore_new_sqlite, UnsafeBackup
from app.rbac_v91 import ROUTE_ROLES, allowed_roles_for, EXTERNAL_NETWORK_ROUTES
from app.staff_v9 import totp_at, matching_counter

PASSWORD = 'An-Isolated-V91-Password-For-Tests-2026'


def calls_dep(node, wanted):
    return getattr(node, 'call', None) == wanted or any(
        calls_dep(child, wanted) for child in getattr(node, 'dependencies', ())
    )


def test_admin_routes_have_exact_fail_closed_policy():
    declared=set()
    for route in main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if calls_dep(route.dependant, main.auth):
            for method in route.methods:
                declared.add((method, route.path))
    assert declared == set(ROUTE_ROLES), {
        'missing_policy': sorted(declared - set(ROUTE_ROLES)),
        'stale_policy': sorted(set(ROUTE_ROLES) - declared)
    }
    # Never allow finance to edit supplier base prices via variant or bulk tools.
    for target in (
        ('PATCH', '/api/admin/variants/{variant_id}'),
        ('POST', '/api/admin/bulk/apply'),
        ('POST', '/api/admin/prom/apply')
    ):
        assert allowed_roles_for(*target) == frozenset({'owner'})
    assert allowed_roles_for('GET','/api/admin/not-listed-in-rbac') is None


@pytest.fixture
def strict_env(monkeypatch):
    monkeypatch.setenv('BEERSHOP_V9_STAFF_AUTH', '1')
    monkeypatch.setenv('BEERSHOP_V91_ENFORCE_RBAC', '1')
    monkeypatch.setenv('BEERSHOP_V91_STAGING_ACK', 'ISOLATED_STAGING_ONLY')
    monkeypatch.setenv('BEERSHOP_V9_MFA_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't'*40)
    monkeypatch.setenv('BEERSHOP_V9_AUDIT_TOKEN', 'a'*40)
    monkeypatch.setenv('KEYCRM_SYNC_ENABLED', '0')
    monkeypatch.setenv('PRIVATBANK_READ_ENABLED', '0')
    monkeypatch.setattr(main, 'PERSISTENT_URL', '')
    yield monkeypatch


def create_login(client, role):
    name='v91'+role[:3]+uuid4().hex[:10]
    with main.SessionLocal() as db:
        user,uri=main.create_staff_user_v9(db, name, PASSWORD, role)
    seed=uri.split('secret=')[1].split('&')[0]
    code=totp_at(seed,int(time.time()//30))
    resp=client.post('/api/admin/v9/staff/login',json={
        'username': name,'password':PASSWORD,'otp':code
    })
    assert resp.status_code==200, (role,resp.text)
    return user.id, {'Authorization':'Bearer '+resp.json()['access_token']}


def test_strict_rbac_rejects_legacy_password_and_missing_staging_ack(strict_env):
    with TestClient(main.app) as client:
        _,owner=create_login(client,'owner')
        assert client.get('/api/admin/products',headers={
            'Authorization':'Bearer '+'t'*40}).status_code==401
        assert client.get('/api/admin/v9/readiness',headers={
            'Authorization':'Bearer '+'a'*40}).status_code==401
        strict_env.delenv('BEERSHOP_V91_STAGING_ACK')
        assert client.get('/api/admin/products',headers=owner).status_code==503
        strict_env.setenv('BEERSHOP_V91_STAGING_ACK','ISOLATED_STAGING_ONLY')
        strict_env.setattr(main,'PERSISTENT_URL','postgresql://production-should-not-be-used')
        assert client.get('/api/admin/products',headers=owner).status_code==503
        strict_env.setattr(main,'PERSISTENT_URL','')
        assert client.get('/api/admin/products',headers=owner).status_code==200


def test_role_separation_for_crm_catalog_orders_finance_and_backup(strict_env, tmp_path):
    with TestClient(main.app) as client:
        actors={}
        for role in ('owner','catalog','operations','finance','auditor'):
            actors[role]=create_login(client,role)
        owner=actors['owner'][1]
        catalog=actors['catalog'][1]
        operations=actors['operations'][1]
        finance=actors['finance'][1]
        auditor=actors['auditor'][1]

        # Strong positive checks: staff role grants exactly expected GET scopes.
        assert client.get('/api/admin/products',headers=catalog).status_code==200
        assert client.get('/api/admin/inventory/summary',headers=catalog).status_code==200
        assert client.get('/api/admin/crm/summary',headers=operations).status_code==200
        assert client.get('/api/admin/orders',headers=operations).status_code==200
        assert client.get('/api/admin/orders',headers=finance).status_code==200
        assert client.get('/api/admin/pricing/policy',headers=finance).status_code==200
        assert client.get('/api/admin/v9/readiness',headers=auditor).status_code==200
        assert client.get('/api/admin/maintenance/report',headers=auditor).status_code==200

        # No catalog staff may view customer PII, change prices or confirm bank money.
        assert client.get('/api/admin/crm/orders',headers=catalog).status_code==403
        assert client.get('/api/admin/orders',headers=catalog).status_code==403
        assert client.get('/api/admin/pricing/policy',headers=catalog).status_code==403
        assert client.get('/api/admin/maintenance/export',headers=catalog).status_code==403
        assert client.get('/api/admin/crm/customers',headers=finance).status_code==403
        assert client.get('/api/admin/products',headers=finance).status_code==403
        assert client.get('/api/admin/pricing/policy',headers=operations).status_code==403
        assert client.get('/api/admin/maintenance/export',headers=auditor).status_code==403
        assert client.get('/api/admin/crm/customers',headers=auditor).status_code==403
        assert client.get('/api/admin/v9/readiness',headers=finance).status_code==403

        # Export privileged and still an *unencrypted* archive: verify only offline.
        export=client.get('/api/admin/maintenance/export',headers=owner)
        assert export.status_code==200, export.text[:240]
        snapshot=export.content
        verified=verify_snapshot(snapshot,main.Base.metadata)
        assert verified['total_rows']>=1
        target=tmp_path/'isolated-rehearsal.db'
        restored=rehearsal_restore_new_sqlite(snapshot,main.Base.metadata,target)
        assert restored['ok'] is True
        assert target.exists()
        tampered=json.loads(snapshot)
        tampered['tables']['stores'][0]['name']='Not the original'
        with pytest.raises(UnsafeBackup):
            verify_snapshot(json.dumps(tampered).encode(),main.Base.metadata)
        # Hashes and encrypted MFA data are sensitive; no bearer tokens in exports.
        for role in actors:
            assert actors[role][1]['Authorization'].split(' ',1)[1].encode() not in snapshot

        # Explicitly disable all external connector calls in strict rehearsal.
        assert client.get('/api/admin/connectors41/keycrm/sources',
                          headers=operations).status_code==503
        assert client.get('/api/admin/connectors41/nova-poshta/tracking/1',
                          headers=operations).status_code==503

        # The role check for any price-changing variant/import path is enforced
        # before its handler can mutate records.
        assert client.patch('/api/admin/variants/999999',
                            headers=catalog,json={'stock':5}).status_code==403
        assert client.post('/api/admin/bulk/apply',
                           headers=catalog,json={'changes':[]}).status_code==403


def test_all_sessions_can_be_revoked_and_owner_can_revoke_staff(strict_env):
    with TestClient(main.app) as client:
        owner_id,owner=create_login(client,'owner')
        ops_id,operator=create_login(client,'operations')
        assert client.get('/api/admin/orders',headers=operator).status_code==200
        revocation=client.post(
            f'/api/admin/v9/staff/users/{ops_id}/revoke-sessions',headers=owner)
        assert revocation.status_code==200, revocation.text
        assert revocation.json()['revoked_sessions'] >= 1
        assert client.get('/api/admin/orders',headers=operator).status_code==401
        assert client.get('/api/admin/v9/staff/me',headers=operator).status_code==401
        # Revoking all own sessions invalidates *current* session too.
        self_revoke=client.post('/api/admin/v9/staff/sessions/revoke-all',headers=owner)
        assert self_revoke.status_code==200
        assert self_revoke.json()['all_current_sessions_closed'] is True
        assert client.get('/api/admin/products',headers=owner).status_code==401
        assert client.get('/api/admin/v9/staff/me',headers=owner).status_code==401


def test_expired_sessions_and_disabled_users_cannot_access_admin(strict_env):
    with TestClient(main.app) as client:
        user_id,finance=create_login(client,'finance')
        assert client.get('/api/admin/pricing/policy',headers=finance).status_code==200
        with main.SessionLocal.begin() as db:
            user=db.get(main.StaffUserV9,user_id)
            user.active=False
        assert client.get('/api/admin/pricing/policy',headers=finance).status_code==401
        user_id,owner=create_login(client,'owner')
        with main.SessionLocal.begin() as db:
            from sqlalchemy import select
            token_hash=__import__('hashlib').sha256(owner['Authorization'].split(' ',1)[1].encode()).hexdigest()
            session=db.scalar(select(main.StaffSessionV9).where(main.StaffSessionV9.token_hash==token_hash))
            session.expires_at=int(time.time())-1
        assert client.get('/api/admin/maintenance/export',headers=owner).status_code==401
