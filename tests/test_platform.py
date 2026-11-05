import os
from uuid import uuid4
os.environ['BEERSHOP_ADMIN_TOKEN'] = 't'*40
from fastapi.testclient import TestClient
from app.main import app

auth = {'Authorization': 'Bearer ' + 't'*40}

def test_catalog_and_checkout():
    with TestClient(app) as c:
        assert c.get('/api/health').json()['version'] == '3.4.0'
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
        oid=order.json()['id']; assert order.json()['total']=='2500.00'
        assert c.post('/api/orders',json=payload).json()['id']==oid
        assert c.get('/api/products/'+slug).json()['variants'][0]['stock']==2
        assert c.get(f'/api/admin/orders/{oid}/invoice',headers=auth).status_code==503
        assert c.patch(f'/api/admin/orders/{oid}/status',headers=auth,json={'status':'cancelled'}).status_code==200
        assert c.get('/api/products/'+slug).json()['variants'][0]['stock']==4
        assert c.patch(f'/api/admin/orders/{oid}/status',headers=auth,json={'status':'fulfilled'}).status_code==409

def test_invalid_stock():
    with TestClient(app) as c:
        assert c.post('/api/orders',json={'request_key':uuid4().hex,'name':'Test Customer','email':'test@example.com','phone':'123456789','delivery_address':'Kyiv office 1','items':[{'variant_id':99999999,'quantity':1}]}).status_code==404
