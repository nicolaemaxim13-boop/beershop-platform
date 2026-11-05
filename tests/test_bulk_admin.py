"""BeerShop Admin 3.1 bulk editor: authentication, preview, atomicity and conflict guard."""
import os
from uuid import uuid4
os.environ['BEERSHOP_ADMIN_TOKEN']='t'*40
from fastapi.testclient import TestClient
from app.main import app
AUTH={'Authorization':'Bearer '+'t'*40}

def test_bulk_catalog_preview_apply_conflict():
    slug='bulk-'+uuid4().hex[:12]
    with TestClient(app) as client:
        assert client.get('/api/admin/bulk/catalog').status_code==401
        cid=client.get('/api/categories').json()[0]['id']
        p=client.post('/api/admin/products',headers=AUTH,
          json={'category_id':cid,'slug':slug,'name':'Test Equipment'}).json()
        v=client.post(f"/api/admin/products/{p['id']}/variants",headers=AUTH,
          json={'sku':slug+'-A','price':'125.00','stock':0,'currency':'USD'}).json()
        rows=client.get('/api/admin/bulk/catalog',headers=AUTH,
          params={'q':slug,'page':1,'limit':10})
        assert rows.status_code==200,rows.text
        assert rows.json()['total']==1
        r=rows.json()['items'][0]
        assert r['currency']=='USD' and r['stock']==0
        assert client.get('/api/admin/bulk/catalog',headers=AUTH,params={'q':slug,'currency':'UAH'}).json()['total']==0
        common={'product_id':p['id'],'variant_id':v['id'],
          'expected_product_active':True,'expected_variant_active':True,
          'expected_price':'125.00','expected_currency':'USD','expected_stock':0}
        unsafe={'changes':[{**common,'currency':'UAH'}]}
        assert client.post('/api/admin/bulk/preview',headers=AUTH,json=unsafe).status_code==422
        assert client.post('/api/admin/bulk/apply',headers=AUTH,json=unsafe).status_code==422
        payload={'changes':[{**common,'price':'599.00','currency':'UAH','stock':5}]}
        preview=client.post('/api/admin/bulk/preview',headers=AUTH,json=payload)
        assert preview.status_code==200,preview.text
        assert preview.json()['applied'] is False
        assert client.get('/api/admin/bulk/catalog',headers=AUTH,params={'q':slug}).json()['items'][0]['stock']==0
        result=client.post('/api/admin/bulk/apply',headers=AUTH,json=payload)
        assert result.status_code==200,result.text
        assert result.json()['applied'] is True
        after=client.get('/api/admin/bulk/catalog',headers=AUTH,params={'q':slug}).json()['items'][0]
        assert after['stock']==5 and after['price']=='599.00' and after['currency']=='UAH'
        assert client.post('/api/admin/bulk/apply',headers=AUTH,json=payload).status_code==409
        assert client.get('/assets/admin-bulk.js').status_code==200
        assert client.get('/assets/admin-bulk.css').status_code==200
        assert 'Масовий редактор' in client.get('/manage').text

def test_bulk_atomic_and_validation():
    slug='atomic-'+uuid4().hex[:12]
    with TestClient(app) as client:
        cid=client.get('/api/categories').json()[0]['id']
        p=client.post('/api/admin/products',headers=AUTH,
          json={'category_id':cid,'slug':slug,'name':'Bundle'}).json()
        def add(n):
            return client.post(f"/api/admin/products/{p['id']}/variants",headers=AUTH,
              json={'sku':slug+n,'price':'200.00','stock':2}).json()
        x,y=add('A'),add('B')
        def row(v,price='200.00',stock=2,new_stock=7):
            return {'product_id':p['id'],'variant_id':v['id'],
              'expected_product_active':True,'expected_variant_active':True,
              'expected_price':price,'expected_currency':'UAH',
              'expected_stock':stock,'stock':new_stock}
        payload={'changes':[row(x),row(y,new_stock=8)]}
        assert client.post('/api/admin/bulk/preview',headers=AUTH,json=payload).status_code==200
        bad={'changes':[row(x),row(y,price='999.00')]}
        assert client.post('/api/admin/bulk/apply',headers=AUTH,json=bad).status_code==409
        rows=client.get('/api/admin/bulk/catalog',headers=AUTH,params={'q':slug}).json()['items']
        assert all(r['stock']==2 for r in rows)
        assert client.post('/api/admin/bulk/apply',headers=AUTH,json=payload).status_code==200
        assert sorted(r['stock'] for r in client.get('/api/admin/bulk/catalog',headers=AUTH,
          params={'q':slug}).json()['items'])==[7,8]
        assert client.post('/api/admin/bulk/apply',headers=AUTH,
          json={'changes':[row(x),row(x)]}).status_code==422
        assert client.post('/api/admin/bulk/apply',json=payload).status_code==401
