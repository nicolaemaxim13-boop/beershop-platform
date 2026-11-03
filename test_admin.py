import os
from uuid import uuid4
os.environ['BEERSHOP_ADMIN_TOKEN']='t'*40
from fastapi.testclient import TestClient
from app.main import app
AUTH={'Authorization':'Bearer '+'t'*40}

def test_admin_workflow():
    with TestClient(app) as c:
        assert c.get('/manage').status_code==200
        assert c.get('/api/admin/products').status_code==401
        cats=c.get('/api/categories').json()
        slug='admin-'+uuid4().hex[:12]
        p=c.post('/api/admin/products',headers=AUTH,json={'category_id':cats[0]['id'],'slug':slug,'name':'Test Equipment'})
        assert p.status_code==201,p.text
        pid=p.json()['id']
        assert c.patch(f'/api/admin/products/{pid}',headers=AUTH,json={'active':False}).json()['active'] is False
        assert any(x['id']==pid for x in c.get('/api/admin/products',headers=AUTH).json())
        content='category_slug,product_slug,product_name,sku,price,stock\ncouplers,head,Head,H-A,100.00,5\n'
        r=c.post('/api/admin/import/preview',headers=AUTH,files={'file':('test.csv',content,'text/csv')})
        assert r.status_code==200,r.text
        assert r.json()['count']==1
        assert c.post('/api/admin/import/preview',headers=AUTH,files={'file':('bad.csv','bad\nx','text/csv')}).status_code==422
