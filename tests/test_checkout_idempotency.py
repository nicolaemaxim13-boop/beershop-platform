"""Public checkout idempotency must never disclose or mutate a different order."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


AUTH = {'Authorization': 'Bearer ' + 't' * 40}


def test_checkout_replay_requires_identical_buyer_basket_and_total(monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    key = uuid4().hex
    with TestClient(app) as client:
        category_id = client.get('/api/categories').json()[0]['id']
        product = client.post('/api/admin/products', headers=AUTH, json={
            'category_id': category_id, 'slug': 'idem-' + key,
            'name': 'Idempotency synthetic product',
        })
        assert product.status_code == 201, product.text
        variant = client.post(
            f"/api/admin/products/{product.json()['id']}/variants",
            headers=AUTH,
            json={'sku': 'idem-' + key, 'price': '100.00', 'stock': 3},
        )
        assert variant.status_code == 201, variant.text
        variant_id = variant.json()['id']
        body = {
            'request_key': key, 'name': 'Test buyer',
            'email': 'private-' + key + '@example.test', 'phone': '0501234567',
            'delivery_address': 'Synthetic test branch',
            'items': [{'variant_id': variant_id, 'quantity': 1}],
            'expected_total': '100.00',
        }
        created = client.post('/api/orders', json=body)
        assert created.status_code == 201, created.text
        original_id = created.json()['id']

        replay = client.post('/api/orders', json=body)
        assert replay.status_code == 201, replay.text
        assert replay.json()['id'] == original_id

        changed_requests = [
            {**body, 'name': 'Different person'},
            {**body, 'email': 'other@example.test'},
            {**body, 'phone': '0678901234'},
            {**body, 'delivery_address': 'Other branch'},
            {**body, 'items': [{'variant_id': variant_id, 'quantity': 2}]},
            {**body, 'expected_total': '200.00'},
        ]
        for changed in changed_requests:
            result = client.post('/api/orders', json=changed)
            assert result.status_code == 409, result.text
            assert body['email'] not in result.text
            assert body['phone'] not in result.text
            assert 'delivery_address' not in result.text

        snapshot = client.get(f'/api/admin/inventory/{variant_id}', headers=AUTH)
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()['available'] == 2
        assert snapshot.json()['reserved'] == 1
        assert [m['event'] for m in snapshot.json()['movements']] == ['reserved']
