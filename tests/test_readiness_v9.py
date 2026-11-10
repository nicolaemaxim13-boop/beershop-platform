"""v9 read-only safety audit, staging checkout switches and UA IBAN verification.

All created products/orders are synthetic and confined to isolated CI databases.
No bank, live provider, Render or production Neon access is used.
"""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, func

import app.main as main
from app.readiness_v9 import valid_ukrainian_iban, recent_manual_rate

AUTH = {'Authorization': 'Bearer ' + 't' * 40}


def fake_iban():
    bban = '1234567890123456789012345'  # 25 synthetic digits
    digits = bban + '301000'  # moved UA00 to the end (UA=3010)
    check = 98 - (int(digits) % 97)
    return 'UA' + f'{check:02d}' + bban


def test_ua_iban_checksum_and_negative_variants():
    iban = fake_iban()
    assert len(iban) == 29 and valid_ukrainian_iban(iban)
    assert valid_ukrainian_iban(iban.lower())
    assert valid_ukrainian_iban(iban[:4] + ' ' + iban[4:])
    assert not valid_ukrainian_iban('UA' + ('0' * 27))
    assert not valid_ukrainian_iban('UA' + ('1' * 26))
    assert not valid_ukrainian_iban(iban[:2] + ('99' if iban[2:4] != '99' else '98') + iban[4:])
    assert not valid_ukrainian_iban('DE' + iban[2:])
    assert not valid_ukrainian_iban('UA' + ('A' * 27))


def test_manual_rate_must_be_recent():
    now = datetime.now(timezone.utc)
    policy = lambda checked, enabled=True: SimpleNamespace(
        enabled=enabled, checked_at=checked.isoformat())
    assert recent_manual_rate(policy(now - timedelta(hours=23)), now)
    assert not recent_manual_rate(policy(now - timedelta(hours=25)), now)
    assert not recent_manual_rate(policy(now + timedelta(minutes=1)), now)
    assert not recent_manual_rate(policy(now - timedelta(hours=1), enabled=False), now)
    assert not recent_manual_rate(None, now)


def test_readiness_requires_auth_and_does_not_modify_database(monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    monkeypatch.setenv('BEERSHOP_IBAN', 'UA' + '0' * 27)
    monkeypatch.setenv('BEERSHOP_PAYEE', 'Testing merchant only')
    with TestClient(main.app) as c:
        assert c.get('/api/admin/v9/readiness').status_code == 401
        before = c.get('/api/admin/maintenance/report', headers=AUTH).json()['counts']
        with main.SessionLocal() as db:
            order_count = db.scalar(select(func.count()).select_from(main.Order))
            stock_total = db.scalar(select(func.sum(main.Variant.stock)))
        response = c.get('/api/admin/v9/readiness', headers=AUTH)
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.headers['referrer-policy'] == 'no-referrer'
        assert response.headers['x-frame-options'] == 'DENY'
        assert response.headers['permissions-policy'].startswith('camera=()')
        assert response.headers['x-content-type-options'] == 'nosniff'
        data = response.json()
        assert data['mode'] == 'READ_ONLY_AUDIT'
        assert data['sales_ready'] is False
        assert data['mutations'] == data['external_payment_calls'] == 0
        assert data['summary']['products'] >= 0
        assert data['summary']['blockers'] >= 3
        assert data['summary']['orders'] >= 0
        assert {'security','inventory','pricing','iban','legal','data'} <= {
            x['group'] for x in data['checks']}
        checks = {x['id']: x for x in data['checks']}
        assert checks['bank_details']['status'] == 'block'
        assert checks['physical_stock']['status'] == 'block'
        assert checks['bank_confirmation']['status'] == 'block'
        assert checks['backup_restore']['status'] == 'block'
        assert checks['individual_accounts']['status'] == 'block'
        assert checks['consumer_law']['status'] == 'block'
        body = response.text
        assert 'Testing merchant only' not in body
        assert 'UA000000000000000000000000000' not in body
        assert 'tttttttttttt' not in body
        assert c.get('/api/admin/maintenance/report', headers=AUTH).json()['counts'] == before
        with main.SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(main.Order)) == order_count
            assert db.scalar(select(func.sum(main.Variant.stock))) == stock_total


def test_iban_invoice_fail_closed_and_staging_order_guards(monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    slug = 'v9-check-' + uuid4().hex[:12]
    with TestClient(main.app) as c:
        category = c.get('/api/categories').json()[0]['id']
        p = c.post('/api/admin/products', headers=AUTH, json={
            'name': 'Staging v9 safety fixture', 'slug': slug, 'category_id': category})
        assert p.status_code == 201, p.text
        v = c.post('/api/admin/products/' + str(p.json()['id']) + '/variants',
                   headers=AUTH,json={'sku':slug,'price':'100.00',
                                       'currency':'UAH','stock':2})
        assert v.status_code == 201, v.text
        vid = v.json()['id']
        line = {'variant_id':vid,'quantity':1}
        payload = {'request_key': uuid4().hex, 'name':'CI test',
                   'email':'synthetic@example.test','phone':'0501234567',
                   'delivery_address':'Isolated CI test address',
                   'items':[line]}
        quote = c.post('/api/quote', json={'items':[line]})
        assert quote.status_code == 200, quote.text
        payload['expected_total'] = quote.json()['total']
        monkeypatch.setenv('BEERSHOP_V9_DISABLE_ORDER_CREATION', '1')
        denied = c.post('/api/orders', json=payload)
        assert denied.status_code == 503, denied.text
        assert c.get('/api/products/' + slug).json()['variants'][0]['stock'] == 2
        monkeypatch.delenv('BEERSHOP_V9_DISABLE_ORDER_CREATION')
        monkeypatch.setenv('BEERSHOP_V9_REQUIRE_QUOTE_TOTAL', '1')
        missing = c.post('/api/orders', json={**payload,
                             'request_key':uuid4().hex,'expected_total':None})
        assert missing.status_code == 428
        assert c.get('/api/products/' + slug).json()['variants'][0]['stock'] == 2
        ok = c.post('/api/orders', json=payload)
        assert ok.status_code == 201, ok.text
        oid = ok.json()['id']
        assert ok.json()['total'] == payload['expected_total']
        assert c.get('/api/products/' + slug).json()['variants'][0]['stock'] == 1
        monkeypatch.setenv('BEERSHOP_IBAN', 'UA' + ('0' * 27))
        monkeypatch.setenv('BEERSHOP_PAYEE', 'Synthetic operator')
        assert c.get(f'/api/admin/orders/{oid}/invoice',headers=AUTH).status_code == 503
        monkeypatch.setenv('BEERSHOP_IBAN', fake_iban())
        invoiced = c.get(f'/api/admin/orders/{oid}/invoice',headers=AUTH)
        assert invoiced.status_code == 200, invoiced.text
        assert invoiced.json()['iban'] == fake_iban()
        assert invoiced.json()['total'] == payload['expected_total']


def test_audit_assets_and_admin_page(monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    with TestClient(main.app) as c:
        admin = c.get('/manage')
        assert admin.status_code == 200
        assert 'Готовність v9' in admin.text
        assert 'id="readiness9-checks"' in admin.text
        assert '/assets/readiness-v9.js' in admin.text
        for name, media in [('readiness-v9.js','javascript'),('readiness-v9.css','css')]:
            r = c.get('/assets/' + name)
            assert r.status_code == 200
            assert media in r.headers['content-type']
            assert r.headers['cache-control'] == 'no-store'
        assert c.get('/api/health').headers['x-frame-options'] == 'DENY'
