"""BeerShop v7: full-catalog ordering uses quoted UAH retail, not raw USD costs."""
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import app

AUTH = {'Authorization': 'Bearer ' + 't' * 40}


def test_catalog_sort_filters_pagination_and_search(monkeypatch):
    import app.main as main
    fixed_fx = {
        'rate': Decimal('50'),
        'baseline_rate': Decimal('45'),
        'effective_date': '2026-11-10',
        'source': 'test-fixed-quote',
    }
    monkeypatch.setattr(main, 'pricing_context', lambda: fixed_fx)
    stamp = 'v7sort' + uuid4().hex[:10]
    cases = [
        ('Alpha', '10.00', 'USD', 3, 'supplyusd'),
        ('Bravo', '540.00', 'UAH', 2, 'v7secret'),
        ('Charlie', '20.00', 'USD', 4, 'upsupply'),
        ('Delta', '180.00', 'UAH', 0, 'outstock'),
        ('Echo', '800.00', 'UAH', 1, 'in-stock'),
    ]
    expected = {}
    with TestClient(app) as client:
        category = client.get('/api/categories').json()[0]['id']
        for name, amount, currency, stock, sku_name in cases:
            product = client.post('/api/admin/products', headers=AUTH, json={
                'category_id': category, 'slug': f'{stamp}-{name.lower()}',
                'name': f'{stamp} {name}', 'description': 'Synthetic fixture',
            })
            assert product.status_code == 201, product.text
            variant = client.post(f"/api/admin/products/{product.json()['id']}/variants",
                headers=AUTH, json={'sku': f'{sku_name}-{stamp}', 'price': amount,
                                    'currency': currency, 'stock': stock,
                                    'attributes': {}})
            assert variant.status_code == 201, variant.text
            expected[name] = product.json()['slug']

        def names(sort, *, limit=12, page=1, in_stock=False, q=stamp):
            response = client.get('/api/products', params={
                'sort': sort, 'q': q, 'in_stock': in_stock,
                'limit': limit, 'page': page,
            })
            assert response.status_code == 200, response.text
            obj = response.json()
            return [row['slug'] for row in obj['items']], obj

        ascending, obj = names('price_asc')
        # Delta 180 UAH is converted at 50/45 => 200 UAH,
        # Alpha USD10 => 500 UAH, Bravo UAH540 => 600 UAH,
        # Echo UAH800 => 888.89 UAH, Charlie USD20 => 1000 UAH.
        assert ascending == [expected[n] for n in ('Delta', 'Alpha', 'Bravo', 'Echo', 'Charlie')]
        assert obj['sort'] == 'price_asc'
        assert obj['total'] == 5
        descending, _ = names('price_desc')
        assert descending == list(reversed(ascending))

        first, page_one = names('price_asc', limit=2, page=1)
        second, page_two = names('price_asc', limit=2, page=2)
        last, page_three = names('price_asc', limit=2, page=3)
        assert first + second + last == ascending
        assert page_one['total'] == page_two['total'] == page_three['total'] == 5

        stocked, filtered = names('price_asc', in_stock=True)
        assert expected['Delta'] not in stocked
        assert stocked == [expected[n] for n in ('Alpha', 'Bravo', 'Echo', 'Charlie')]
        assert filtered['total'] == 4

        alpha, _ = names('name_asc')
        assert alpha == [expected[n] for n in ('Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo')]
        reverse_alpha, _ = names('name_desc')
        assert reverse_alpha == list(reversed(alpha))

        sku_match, found = names('newest', q='V7SECRET-' + stamp)
        assert sku_match == [expected['Bravo']]
        assert found['total'] == 1
        slug_match, _ = names('newest', q=expected['Charlie'])
        assert slug_match == [expected['Charlie']]
        wildcard, _ = names('newest', q='%_NONEXISTENT')
        assert wildcard == []
        assert client.get('/api/products', params={'sort': 'DROP TABLE variants'}).status_code == 422
