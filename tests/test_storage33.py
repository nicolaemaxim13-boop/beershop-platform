"""One-time copy and protected backups; isolated test databases."""
import json
import os
import pytest
from sqlalchemy import create_engine, select, func
from fastapi.testclient import TestClient
from app.main import Base, Store, Category, Product, Variant, Order, OrderItem, app
from app.storage import migrate_once

def setup(path):
    e=create_engine('sqlite:///'+str(path))
    Base.metadata.create_all(e)
    return e

def test_copies_and_preserves_order_ids(tmp_path):
    src=setup(tmp_path/'source.db'); dst=setup(tmp_path/'target.db')
    with src.begin() as c:
        c.execute(Store.__table__.insert(),[{'id':1,'slug':'beershop','name':'Shop'}])
        c.execute(Category.__table__.insert(),[{'id':3,'store_id':1,'slug':'coolers','name':'Coolers'}])
        c.execute(Product.__table__.insert(),[{'id':4,'store_id':1,'category_id':3,
                'slug':'model-a','name':'Cooler A','active':True}])
        c.execute(Variant.__table__.insert(),[{'id':5,'store_id':1,'product_id':4,
               'sku':'A-5','price':'125.50','stock':7,'currency':'UAH','active':True}])
        c.execute(Order.__table__.insert(),[{'id':10,'store_id':1,'request_key':'abcdefgh',
               'name':'Test Customer','email':'test@example.org','phone':'123456789',
               'delivery_address':'Kyiv','total':'125.50'}])
        c.execute(OrderItem.__table__.insert(),[{'id':11,'order_id':10,'variant_id':5,
               'sku':'A-5','name':'Cooler A','quantity':1,'unit_price':'125.50'}])
    result=migrate_once(Base.metadata,str(src.url),dst)
    assert result['products']==1 and result['orders']==1 and result['order_items']==1
    with dst.connect() as c:
        assert c.scalar(select(func.count()).select_from(Variant))==1
        assert str(c.execute(select(Variant.__table__.c.price)).scalar_one())=='125.50'
        assert c.execute(select(Order.__table__.c.name)).scalar_one()=='Test Customer'
    with src.begin() as c:
        c.execute(Product.__table__.insert(),[{'id':8,'store_id':1,'category_id':3,
               'slug':'late','name':'Late product','active':True}])
    assert migrate_once(Base.metadata,str(src.url),dst)==result
    with dst.connect() as c:
        assert c.scalar(select(func.count()).select_from(Product))==1

def test_existing_target_not_overwritten(tmp_path):
    origin=setup(tmp_path/'origin.db'); target=setup(tmp_path/'target.db')
    with target.begin() as c:
        c.execute(Store.__table__.insert(),[{'id':1,'slug':'existing','name':'Existing'}])
    with pytest.raises(RuntimeError,match='unmarked data'):
        migrate_once(Base.metadata,str(origin.url),target)

def test_report_and_backup_are_admin_only():
    os.environ['BEERSHOP_ADMIN_TOKEN']='t'*40
    auth={'Authorization':'Bearer '+'t'*40}
    with TestClient(app) as c:
        assert c.get('/api/admin/maintenance/report').status_code==401
        assert c.get('/api/admin/maintenance/export').status_code==401
        report=c.get('/api/admin/maintenance/report',headers=auth)
        assert report.status_code==200
        assert 'products' in report.json()['counts']
        res=c.get('/api/admin/maintenance/export',headers=auth)
        assert res.status_code==200
        assert res.headers['cache-control']=='private, no-store'
        assert json.loads(res.content)['format']=='beershop-snapshot-v1'
        assert c.get('/assets/admin-backup.js').status_code==200
        assert 'Резервні копії' in c.get('/manage').text
