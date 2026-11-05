"""Mobile BeerShop catalog: authenticated pagination and category updates."""
import os
from uuid import uuid4
os.environ['BEERSHOP_ADMIN_TOKEN']='t'*40
from fastapi.testclient import TestClient
from app.main import app
AUTH={'Authorization':'Bearer '+'t'*40}

def test_mobile_catalog_and_categories():
    s=uuid4().hex[:10]
    with TestClient(app) as c:
        assert c.get('/api/admin/catalog32').status_code==401
        assert c.get('/api/admin/categories32').status_code==401
        category=c.post('/api/admin/categories',headers=AUTH,json={'slug':'mob-'+s,'name':'Охолоджувачі'})
        assert category.status_code==201,category.text
        cid=category.json()['id']
        p=c.post('/api/admin/products',headers=AUTH,json={
            'category_id':cid,'slug':'mob-'+s,'name':'Пивний охолоджувач '+s})
        assert p.status_code==201,p.text
        pid=p.json()['id']
        v=c.post('/api/admin/products/'+str(pid)+'/variants',headers=AUTH,
            json={'sku':'PRD-'+s,'price':'88.50','stock':2,'currency':'USD'})
        assert v.status_code==201,v.text
        rows=c.get('/api/admin/catalog32',headers=AUTH,params={'q':'PRD-'+s,'page':1,'limit':2})
        assert rows.status_code==200,rows.text
        assert rows.json()['total']==1
        assert rows.json()['items'][0]['variants']==1
        assert c.get('/api/admin/catalog32',headers=AUTH,params={'category_id':cid}).json()['total']==1
        details=c.get('/api/admin/catalog32/'+str(pid),headers=AUTH).json()
        assert details['variants'][0]['stock']==2
        renamed=c.patch('/api/admin/categories32/'+str(cid),headers=AUTH,
            json={'name':'Пивні охолоджувачі'})
        assert renamed.status_code==200
        assert renamed.json()['slug']=='mob-'+s
        assert any(x['id']==cid and x['products']==1 for x in c.get('/api/admin/categories32',headers=AUTH).json())
        result=c.patch('/api/admin/products/'+str(pid),headers=AUTH,
            json={'name':'Оновлений охолоджувач','image_url':'https://example.org/img.jpg','category_id':cid})
        assert result.status_code==200,result.text
        assert result.json()['image_url']=='https://example.org/img.jpg'
        assert result.json()['variants'][0]['stock']==2
        assert result.json()['variants'][0]['price']=='88.50'
        assert c.patch('/api/admin/categories32/999999999',headers=AUTH,
            json={'name':'Нова назва'}).status_code==404
        assert c.get('/assets/admin-catalog32.js').status_code==200
        assert c.get('/assets/admin-catalog32.css').status_code==200
        assert 'Каталог 3.2' in c.get('/manage').text
