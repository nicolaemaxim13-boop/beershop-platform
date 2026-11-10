"""BeerShop admin-configured, server-authoritative FX policy regression tests.

Test data and products are local CI fixtures only; no production DB access.
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

os.environ['BEERSHOP_ADMIN_TOKEN'] = 't' * 40

from fastapi.testclient import TestClient
from app.main import app, PricingPolicy, SessionLocal
from app.pricing import calculate_sell_rate

AUTH = {'Authorization': 'Bearer ' + 't' * 40}


def values(**changes):
    data = {
        'enabled': False,
        'bank_card_rate': '46.08',
        'cash_usd_buy_rate': '46.75',
        'single_tax_percent': '5',
        'military_percent': '1',
        'withdrawal_percent': '1',
        'profit_percent': '0',
        'confirm_rates': False,
    }
    data.update(changes)
    return data


def test_pricing_math_and_no_double_charge_for_tax_or_fee():
    result = calculate_sell_rate('46.08', '46.75', '5', '1', '1', '0')
    assert result['site_rate'] == Decimal('50.236407')
    assert result['markup_percent'] == Decimal('9.02')
    # The selling price replenishes $100 of replacement goods, after all
    # transaction-percentage deductions at illustrative rates.
    proceeds = result['site_rate'] * Decimal('100') * Decimal('.94') * Decimal('.99')
    assert proceeds >= Decimal('4675.00')
    # Profit 10% is a surcharge on replacement cost, not on gross receipts.
    profit = calculate_sell_rate('46.08', '46.75', '5', '1', '1', '10')
    assert profit['site_rate'] > result['site_rate']
    assert profit['site_rate'] * Decimal('100') * Decimal('.94') * Decimal('.99') >= Decimal('5142.50')


def test_admin_pricing_activation_and_server_quoted_checkout():
    with TestClient(app) as client:
        try:
            assert client.get('/api/admin/pricing/policy').status_code == 401
            assert client.post('/api/admin/pricing/preview', json=values()).status_code == 401
            assert client.put('/api/admin/pricing/policy', json=values()).status_code == 401
            assert client.get('/assets/pricing-admin.js').status_code == 200
            assert 'id="pricing-form"' in client.get('/manage').text
            initial = client.get('/api/admin/pricing/policy', headers=AUTH)
            assert initial.status_code == 200
            assert initial.headers['cache-control'] == 'private, no-store'

            preview = client.post('/api/admin/pricing/preview', headers=AUTH,
                                  json=values()).json()
            assert preview['site_rate'] == '50.236407'
            assert preview['markup_percent'] == '9.02'

            # Cannot enable an unverified rate.
            unconfirmed = client.put('/api/admin/pricing/policy', headers=AUTH,
                                     json=values(enabled=True))
            assert unconfirmed.status_code == 422
            assert client.post('/api/admin/pricing/preview', headers=AUTH,
                               json=values(bank_card_rate='0')).status_code == 422
            assert client.post('/api/admin/pricing/preview', headers=AUTH,
                               json=values(single_tax_percent='20', military_percent='25',
                                           withdrawal_percent='25')).status_code == 200

            activated = client.put('/api/admin/pricing/policy', headers=AUTH,
                                   json=values(enabled=True, confirm_rates=True))
            assert activated.status_code == 200, activated.text
            assert activated.json()['enabled'] is True
            fx = client.get('/api/fx/usd-uah').json()
            assert fx['source'] == 'manual-pricing-policy'
            assert fx['rate'] == '50.236407'
            assert fx['baseline_rate'] == fx['rate']
            assert 'manual' in fx['source']

            category_id = client.get('/api/categories').json()[0]['id']
            stem = 'pricing-' + uuid4().hex[:10]
            product = client.post('/api/admin/products', headers=AUTH, json={
                'category_id': category_id, 'name': 'Manual-price test tap',
                'slug': stem,
            })
            assert product.status_code == 201, product.text
            uah_product = client.post('/api/admin/products', headers=AUTH, json={
                'category_id': category_id, 'name': 'Already UAH-priced test tap',
                'slug': stem + '-uah',
            })
            assert uah_product.status_code == 201, uah_product.text
            usd = client.post('/api/admin/products/' + str(product.json()['id']) + '/variants',
                headers=AUTH, json={'sku': stem, 'price': '100.00', 'currency': 'USD', 'stock': 4})
            uah = client.post('/api/admin/products/' + str(uah_product.json()['id']) + '/variants',
                headers=AUTH, json={'sku': stem + '-uah', 'price': '1000.00',
                                    'currency': 'UAH', 'stock': 4})
            assert usd.status_code == 201 and uah.status_code == 201
            usd_item = client.get('/api/products/' + stem).json()['variants'][0]
            uah_item = client.get('/api/products/' + stem + '-uah').json()['variants'][0]
            assert usd_item['price'] == '5023.64' and usd_item['currency'] == 'UAH'
            assert usd_item['source_price'] == '100.00' and usd_item['source_currency'] == 'USD'
            assert uah_item['price'] == '1000.00', 'UAH must not receive an additional markup'

            listing = client.get('/api/products', params={'q': 'Manual-price test tap'})
            assert listing.status_code == 200
            assert any(v['price'] == '5023.64' for p in listing.json()['items'] for v in p['variants'])
            items = [{'variant_id': usd.json()['id'], 'quantity': 1},
                     {'variant_id': uah.json()['id'], 'quantity': 1}]
            quote = client.post('/api/quote', json={'items': items})
            assert quote.status_code == 200, quote.text
            assert quote.json()['total'] == '6023.64'
            order = {'request_key': uuid4().hex, 'name': 'FX Test User',
                     'email': 'pricing-fixture@example.test', 'phone': '0501234567',
                     'delivery_address': 'Kyiv test branch',
                     'expected_total': quote.json()['total'], 'items': items}
            wrong = client.post('/api/orders', json={**order, 'expected_total': '6000.00'})
            assert wrong.status_code == 409
            purchased = client.post('/api/orders', json=order)
            assert purchased.status_code == 201, purchased.text
            assert purchased.json()['total'] == quote.json()['total']
            assert client.post('/api/orders', json=order).json()['id'] == purchased.json()['id']

            with SessionLocal.begin() as db:
                db.get(PricingPolicy, 1).checked_at = (
                    datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
            assert client.get('/api/fx/usd-uah').status_code == 503
            assert client.get('/api/products/' + stem).status_code == 503
            assert client.post('/api/quote', json={'items': items}).status_code == 503
            assert client.post('/api/orders', json={**order, 'request_key': uuid4().hex}).status_code == 503

            disabled = client.put('/api/admin/pricing/policy', headers=AUTH, json=values())
            assert disabled.status_code == 200
            assert disabled.json()['enabled'] is False
        finally:
            # Tests share a local DB; do not let the manual policy affect unrelated tests.
            with SessionLocal.begin() as db:
                policy = db.get(PricingPolicy, 1)
                if policy:
                    policy.enabled = False


def test_pricing_policy_input_validation():
    with TestClient(app) as client:
        for bad in (values(single_tax_percent='26'), values(military_percent='-1'),
                    values(withdrawal_percent='100'), values(profit_percent='101'),
                    values(bank_card_rate='9999')):
            r = client.post('/api/admin/pricing/preview', headers=AUTH, json=bad)
            assert r.status_code == 422, r.text
