import os
from uuid import uuid4
os.environ['BEERSHOP_ADMIN_TOKEN'] = 't'*40
from fastapi.testclient import TestClient
from app.main import app

auth = {'Authorization': 'Bearer ' + 't'*40}

def test_catalog_and_checkout():
    with TestClient(app) as c:
        assert c.get('/api/health').json()['version'] == '4.4.0'
        assert c.get('/api/admin/orders').status_code == 401
        category = c.get('/api/categories').json()[0]
        slug = 'test-'+uuid4().hex[:12]
        p = c.post('/api/admin/products', headers=auth, json={'category_id': category['id'], 'slug':slug, 'name':'Тестова забірна головка'}); assert p.status_code == 201, p.text
        pid=p.json()['id']
        v = c.post(f'/api/admin/products/{pid}/variants', headers=auth, json={'sku':slug+'-A','attributes':{'lock':'A'},'price':'1250.00','stock':4}); assert v.status_code == 201, v.text
        vid=v.json()['id']
        assert c.get('/api/products/'+slug).json()['variants'][0]['sku'] == slug+'-A'
        assert c.get('/api/products?q=Тестова').status_code == 200
        payload={'request_key':uuid4().hex,'name':'Test Customer','email':'test@example.com','phone':'+380123456789','delivery_address':'Kyiv office 1','items':[{'variant_id':vid,'quantity':2}]}
        order=c.post('/api/orders',json=payload); assert order.status_code==201,order.text
        oid=order.json()['id']; assert order.json()['total']=='2502.00'
        assert c.post('/api/orders',json=payload).json()['id']==oid
        assert c.get('/api/products/'+slug).json()['variants'][0]['stock']==2
        assert c.get(f'/api/admin/orders/{oid}/invoice',headers=auth).status_code==503
        assert c.patch(f'/api/admin/orders/{oid}/status',headers=auth,json={'status':'cancelled'}).status_code==200
        assert c.get('/api/products/'+slug).json()['variants'][0]['stock']==4
        assert c.patch(f'/api/admin/orders/{oid}/status',headers=auth,json={'status':'fulfilled'}).status_code==409

def test_invalid_stock():
    with TestClient(app) as c:
        assert c.post('/api/orders',json={'request_key':uuid4().hex,'name':'Test Customer','email':'test@example.com','phone':'123456789','delivery_address':'Kyiv office 1','items':[{'variant_id':99999999,'quantity':1}]}).status_code==404



def test_storefront_v5_product_pages_and_stock_filter():
    """Every product page resolves only active merchandise and stock filter is global."""
    with TestClient(app) as client:
        category = client.get('/api/categories').json()[0]
        stem = 'v5-' + uuid4().hex[:12]
        stocked = client.post('/api/admin/products', headers=auth, json={
            'category_id': category['id'], 'slug': stem + '-stocked',
            'name': 'Test beer tap available'
        })
        assert stocked.status_code == 201, stocked.text
        missing = client.post('/api/admin/products', headers=auth, json={
            'category_id': category['id'], 'slug': stem + '-out',
            'name': 'Test beer tap unavailable'
        })
        assert missing.status_code == 201, missing.text
        for product, qty in ((stocked, 2), (missing, 0)):
            vid = client.post('/api/admin/products/' + str(product.json()['id']) + '/variants',
                headers=auth, json={'sku': stem + '-' + str(qty), 'price': '1499.00', 'stock': qty})
            assert vid.status_code == 201, vid.text
        all_items = client.get('/api/products', params={'q': 'Test beer tap', 'in_stock': 'false'}).json()['items']
        assert any(item['id'] == stocked.json()['id'] for item in all_items)
        assert any(item['id'] == missing.json()['id'] for item in all_items)
        available = client.get('/api/products', params={'q': 'Test beer tap', 'in_stock': 'true'})
        assert available.status_code == 200, available.text
        ids = [item['id'] for item in available.json()['items']]
        assert stocked.json()['id'] in ids
        assert missing.json()['id'] not in ids
        page = client.get('/products/' + stem + '-stocked')
        assert page.status_code == 200 and 'BeerShop' in page.text
        assert '/assets/storefront-v5.js' in page.text
        assert client.get('/products/' + stem + '-not-found').status_code == 404
        asset = client.get('/assets/storefront-v5.js')
        assert asset.status_code == 200 and 'javascript' in asset.headers['content-type']
        css = client.get('/assets/storefront-v6.css')
        assert css.status_code == 200 and 'text/css' in css.headers['content-type']
        assert css.headers['cache-control'] == 'no-store'
        assert 'font-size:0' in css.text
        landing = client.get('/')
        assert '/assets/storefront-v6.css' in landing.text
        assert '/assets/storefront-v7.css' in landing.text
        assert '/assets/storefront-v7.js' in landing.text
        assert 'id="sort"' in landing.text
        for name, media in (('storefront-v7.css', 'text/css'),
                            ('storefront-v7.js', 'application/javascript')):
            asset = client.get('/assets/' + name)
            assert asset.status_code == 200
            assert media in asset.headers['content-type']
            assert asset.headers['cache-control'] == 'no-store'
        assert landing.headers['cache-control'].startswith('no-store')
        assert page.headers['cache-control'].startswith('no-store')


def test_replay_key_cannot_disclose_another_buyers_order():
    with TestClient(app) as client:
        category = client.get('/api/categories').json()[0]
        slug = 'repeat-' + uuid4().hex[:12]
        product = client.post('/api/admin/products', headers=auth, json={
            'category_id': category['id'], 'slug': slug, 'name': 'Key isolation test'
        }).json()
        variant = client.post(f"/api/admin/products/{product['id']}/variants", headers=auth, json={
            'sku': slug, 'price': '100.00', 'stock': 3
        }).json()
        payload = {
            'request_key': uuid4().hex, 'name': 'Original Buyer',
            'email': 'private@example.test', 'phone': '0501234567',
            'delivery_address': 'Kyiv branch 1',
            'items': [{'variant_id': variant['id'], 'quantity': 1}],
            'expected_total': '101.00'
        }
        created = client.post('/api/orders', json=payload)
        assert created.status_code == 201, created.text
        assert client.post('/api/orders', json=payload).json()['id'] == created.json()['id']
        for changed in (
            {**payload, 'name': 'Impersonator'},
            {**payload, 'phone': '0123456789'},
            {**payload, 'items': [{'variant_id': variant['id'], 'quantity': 2}]},
            {**payload, 'expected_total': '200.00'},
        ):
            response = client.post('/api/orders', json=changed)
            assert response.status_code == 409, response.text
            assert payload['email'] not in response.text
            assert payload['phone'] not in response.text
        item = client.get('/api/admin/inventory/' + str(variant['id']), headers=auth).json()
        assert item['available'] == 2 and item['reserved'] == 1
