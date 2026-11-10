"""Retail whole-hryvnia upward rounding, quote consistency and source-price safety.

No access to production services; tests run against the isolated CI database.
"""
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main
from app.pricing import round_retail_up

AUTH = {'Authorization': 'Bearer ' + 't' * 40}


def test_retail_rounding_is_upward_and_avoids_trailing_zero():
    cases = (
        ('688.45', '689.00'),
        ('16520.54', '16521.00'),
        ('500.00', '501.00'),
        ('499.01', '501.00'),  # ceil to 500, then move beyond the trailing 0
        ('499.00', '499.00'),
        ('500.01', '501.00'),
        ('100.00', '101.00'),
        ('100.01', '101.00'),
        ('0.01', '1.00'),
        ('0.00', '0.00'),  # zero-cost / free items must remain free
    )
    for raw, expected in cases:
        actual = round_retail_up(raw)
        assert actual == Decimal(expected), (raw, actual)
        assert actual >= Decimal(raw)
        if actual:
            assert actual == actual.to_integral_value()
            assert actual % Decimal(10) != 0
    for bad in ('-1.00', 'NaN', 'Infinity'):
        try:
            round_retail_up(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid retail amount accepted: ' + bad)


def test_unit_prices_round_once_in_catalog_quote_checkout_and_order(monkeypatch):
    # An illustrative fixed FX state; no live API requests and no real orders.
    monkeypatch.setattr(main, 'pricing_context', lambda: {
        'rate': Decimal('40'), 'baseline_rate': Decimal('40'),
        'effective_date': '2026-11-10', 'source': 'test-retail-price'
    })
    stamp = 'rounding-' + uuid4().hex[:12]
    with TestClient(main.app) as client:
        cid = client.get('/api/categories').json()[0]['id']
        catalog_items = []
        for label, raw, currency, result in (
            ('alpha', '688.45', 'UAH', '689.00'),
            ('bravo', '12.50', 'USD', '501.00'),
            ('charlie', '500.50', 'UAH', '501.00'),
        ):
            p = client.post('/api/admin/products', headers=AUTH, json={
                'category_id': cid, 'slug': stamp + '-' + label,
                'name': stamp + ' ' + label,
            })
            assert p.status_code == 201, p.text
            product = p.json()
            v = client.post('/api/admin/products/' + str(product['id']) + '/variants',
                headers=AUTH, json={'sku': stamp + '-' + label,
                                    'price': raw, 'currency': currency, 'stock': 5})
            assert v.status_code == 201, v.text
            catalog = client.get('/api/products/' + product['slug']).json()['variants'][0]
            assert catalog['price'] == result
            assert catalog['currency'] == 'UAH'
            assert catalog['source_price'] == raw
            assert catalog['source_currency'] == currency
            admin = next(item for item in client.get('/api/admin/products', headers=AUTH).json()
                         if item['id'] == product['id'])['variants'][0]
            assert admin['price'] == raw and admin['currency'] == currency
            catalog_items.append({'variant_id': v.json()['id'], 'quantity': 2})

        public = client.get('/api/products', params={'q': stamp, 'sort': 'price_asc'})
        assert public.status_code == 200, public.text
        visible_prices = [Decimal(product['variants'][0]['price']) for product in public.json()['items']]
        assert visible_prices == sorted(visible_prices)

        q = client.post('/api/quote', json={'items': catalog_items})
        assert q.status_code == 200, q.text
        assert [x['unit_price_uah'] for x in q.json()['items']] == ['689.00', '501.00', '501.00']
        assert q.json()['total'] == '3382.00'
        base = {
            'request_key': uuid4().hex,
            'name': 'Test buyer', 'email': 'buyer@example.test',
            'phone': '+380501234567', 'delivery_address': 'Kyiv test pickup',
            'items': catalog_items, 'expected_total': q.json()['total'],
        }
        assert client.post('/api/orders', json={**base, 'expected_total': '3379.00'}).status_code == 409
        placed = client.post('/api/orders', json=base)
        assert placed.status_code == 201, placed.text
        assert placed.json()['total'] == q.json()['total']
        assert sorted(x['unit_price'] for x in placed.json()['items']) == ['501.00', '501.00', '689.00']
        assert client.post('/api/orders', json=base).json()['id'] == placed.json()['id']
