"""Reservations, shipment and stock corrections use the same atomic checkout boundary."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
import app.main as main
import app.connectors41 as connectors

AUTH={'Authorization':'Bearer '+'t'*40}

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN','t'*40)
    with TestClient(main.app) as c:
        yield c

def sku(client,stock=5):
    key=uuid4().hex
    category=client.get('/api/categories').json()[0]['id']
    p=client.post('/api/admin/products',headers=AUTH,json={'name':'Inventory '+key,'slug':key,'category_id':category}).json()
    v=client.post(f'/api/admin/products/{p["id"]}/variants',headers=AUTH,
                  json={'sku':key,'price':'250.00','stock':stock,'currency':'UAH'})
    assert v.status_code==201,v.text
    return v.json()['id']

def payload(vid,quantity=1):
    key=uuid4().hex
    return {'request_key':key,'name':'Inventory test','email':key+'@example.test',
            'phone':'0501234567','delivery_address':'Synthetic branch 1',
            'items':[{'variant_id':vid,'quantity':quantity}]}

def order(client,vid,quantity=1):
    body=payload(vid,quantity)
    r=client.post('/api/orders',json=body)
    assert r.status_code==201,r.text
    return r.json(),body

def stock(client,vid):
    r=client.get(f'/api/admin/inventory/{vid}',headers=AUTH)
    assert r.status_code==200,r.text
    return r.json()

def balances(client,vid,expected):
    data=stock(client,vid)
    assert tuple(data[k] for k in ('available','reserved','on_hand'))==expected
    return data

def pay(client,o):
    r=client.post(f'/api/admin/ops/orders/{o["id"]}/bank-confirm',headers=AUTH,
        json={'transaction_id':uuid4().hex,'amount':o['total'],'currency':'UAH',
              'reference':f'BS-{o["id"]:06}','attestation':'BANK_STATEMENT_CHECKED'})
    assert r.status_code==200,r.text

def waybill(client,o):
    r=client.post(f'/api/admin/ops/orders/{o["id"]}/nova-poshta/waybill',headers=AUTH,
                 json={'tracking_number':str(20490000000000+o['id']),
                       'attestation':'WAYBILL_CHECKED_IN_NOVA_POSHTA'})
    assert r.status_code==200,r.text

def test_checkout_reserves_and_cancel_releases_exactly_once(client):
    vid=sku(client);o,body=order(client,vid,2)
    balances(client,vid,(3,2,5))
    assert client.post('/api/orders',json=body).json()['id']==o['id']
    balances(client,vid,(3,2,5))
    for _ in range(2):
        assert client.patch(f'/api/admin/orders/{o["id"]}/status',headers=AUTH,json={'status':'cancelled'}).status_code==200
    data=balances(client,vid,(5,0,5))
    assert [m['event'] for m in data['movements']]==['released','reserved']

def test_waybill_does_not_ship_and_handover_does_not_subtract_twice(client):
    vid=sku(client);o,_=order(client,vid,2);oid=o['id']
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'confirmed'}).status_code==200
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'picking'}).status_code==409
    pay(client,o)
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'fulfilled'}).status_code==409
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'picking'}).status_code==200
    assert client.get('/api/admin/crm/orders?status=picking',headers=AUTH).json()['total']>=1
    waybill(client,o);balances(client,vid,(3,2,5))
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'fulfilled'}).status_code==409
    for state in ['handed_over','handed_over','delivered','delivered']:
        r=client.patch(f'/api/admin/ops/orders/{oid}/nova-poshta/status',headers=AUTH,
                       json={'status':state,'attestation':'STATUS_CHECKED_IN_NOVA_POSHTA'})
        assert r.status_code==200,r.text
        balances(client,vid,(3,0,3))
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'fulfilled'}).status_code==200
    data=stock(client,vid)
    assert [m['event'] for m in data['movements']]==['shipped','reserved']
    assert client.get(f'/api/admin/crm/orders/{oid}',headers=AUTH).json()['inventory'][0]['state']=='shipped'

def test_provider_direct_delivery_closes_reserve_without_confirming_payment(client,monkeypatch):
    vid=sku(client);o,_=order(client,vid);oid=o['id']
    with main.SessionLocal.begin() as db:
        db.add(main.OrderOperations(order_id=oid,tracking_number=str(20490000000000+oid),delivery_status='created'))
    monkeypatch.setattr(connectors,'track_nova_poshta',lambda n:{'tracking_number':n,'status_code':'9','status':'Synthetic delivered'})
    for _ in range(2):
        r=client.post(f'/api/admin/connectors41/nova-poshta/sync/{oid}',headers=AUTH,
                      json={'attestation':'SYNC_EXISTING_WAYBILL_STATUS'})
        assert r.status_code==200,r.text
    data=balances(client,vid,(4,0,4))
    assert [m['event'] for m in data['movements']]==['shipped','reserved']
    assert client.get(f'/api/admin/crm/orders/{oid}',headers=AUTH).json()['payment_status']=='unpaid'

def test_stock_correction_is_audited_versioned_and_idempotent(client):
    vid=sku(client);order(client,vid,2)
    body={'on_hand':7,'expected_available':3,'expected_reserved':2,'reason':'Synthetic inventory count','request_key':uuid4().hex}
    path=f'/api/admin/inventory/{vid}'
    assert client.patch(path,headers=AUTH,json={**body,'on_hand':1}).status_code==409
    assert client.patch(path,headers=AUTH,json={**body,'expected_reserved':0}).status_code==409
    assert client.patch(path,headers=AUTH,json=body).status_code==200
    assert client.patch(path,headers=AUTH,json=body).status_code==200
    data=balances(client,vid,(5,2,7))
    assert [m['event'] for m in data['movements']].count('stock_count')==1
    assert client.patch(path,headers=AUTH,json={**body,'on_hand':8}).status_code==409
    assert client.patch(path,headers=AUTH,json={**body,'request_key':uuid4().hex}).status_code==409
    assert client.patch(f'/api/admin/variants/{vid}',headers=AUTH,json={'stock':6}).status_code==200
    balances(client,vid,(6,2,8))

def test_correction_detects_changed_reserve_even_when_available_unchanged(client):
    vid=sku(client);o,_=order(client,vid);pay(client,o);waybill(client,o)
    before=stock(client,vid)
    assert client.patch(f'/api/admin/ops/orders/{o["id"]}/nova-poshta/status',headers=AUTH,
                        json={'status':'handed_over','attestation':'STATUS_CHECKED_IN_NOVA_POSHTA'}).status_code==200
    r=client.patch(f'/api/admin/inventory/{vid}',headers=AUTH,json={'on_hand':6,'expected_available':before['available'],
        'expected_reserved':before['reserved'],'reason':'Old snapshot','request_key':uuid4().hex})
    assert r.status_code==409
    balances(client,vid,(4,0,4))

def test_inventory_failure_rolls_back_order_customer_stock_and_movements(client,monkeypatch):
    vid=sku(client);body=payload(vid)
    original=main.inventory_reserve
    def fail(db,o):
        original(db,o)
        raise HTTPException(503,'Synthetic failure')
    monkeypatch.setattr(main,'inventory_reserve',fail)
    assert client.post('/api/orders',json=body).status_code==503
    data=balances(client,vid,(5,0,5))
    assert data['movements']==[]
    with main.SessionLocal() as db:
        assert db.scalar(select(main.Order).where(main.Order.request_key==body['request_key'])) is None

def test_simultaneous_last_unit_cannot_be_oversold(client):
    vid=sku(client,1);barrier=Barrier(2)
    def buy(_):
        body=payload(vid);barrier.wait(timeout=10)
        return client.post('/api/orders',json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(buy,range(2)))
    assert sorted(r.status_code for r in results)==[201,409]
    balances(client,vid,(0,1,1))

def test_failed_handover_rolls_back_reservations_and_delivery(client,monkeypatch):
    vid=sku(client);o,_=order(client,vid);pay(client,o);waybill(client,o)
    original=main.inventory_ship
    def fail(db,order):
        original(db,order)
        raise HTTPException(503,'Synthetic shipment failure')
    monkeypatch.setattr(main,'inventory_ship',fail)
    r=client.patch(f'/api/admin/ops/orders/{o["id"]}/nova-poshta/status',headers=AUTH,
                   json={'status':'handed_over','attestation':'STATUS_CHECKED_IN_NOVA_POSHTA'})
    assert r.status_code==503
    data=balances(client,vid,(4,1,5))
    assert [m['event'] for m in data['movements']]==['reserved']
    with main.SessionLocal() as db:
        assert db.get(main.OrderOperations,o['id']).delivery_status=='created'

def test_bulk_stock_change_is_in_the_inventory_journal(client):
    vid=sku(client)
    with main.SessionLocal() as db:
        v=db.get(main.Variant,vid)
        entry={'variant_id':vid,'product_id':v.product_id,'expected_product_active':True,
               'expected_variant_active':True,'expected_price':'250.00','expected_currency':'UAH',
               'expected_stock':5,'stock':7}
    r=client.post('/api/admin/bulk/apply',headers=AUTH,json={'changes':[entry]})
    assert r.status_code==200,r.text
    data=balances(client,vid,(7,0,7))
    assert data['movements'][0]['available_delta']==2

def test_other_store_inventory_cannot_be_read_or_changed(client):
    with main.SessionLocal.begin() as db:
        if not db.get(main.Store,2):
            db.add(main.Store(id=2,slug='other',name='Other'));db.flush()
        key=uuid4().hex
        category=main.Category(store_id=2,slug=key,name='Other');db.add(category);db.flush()
        product=main.Product(store_id=2,category_id=category.id,slug=key,name='Other');db.add(product);db.flush()
        v=main.Variant(store_id=2,product_id=product.id,sku=key,price='1.00',stock=3);db.add(v);db.flush();vid=v.id
    assert client.get(f'/api/admin/inventory/{vid}',headers=AUTH).status_code==404
    assert client.get('/api/admin/inventory',headers=AUTH,params={'q':key}).json()['total']==0
    assert client.patch(f'/api/admin/inventory/{vid}',headers=AUTH,json={'on_hand':5,'expected_available':3,
        'expected_reserved':0,'reason':'Not this store','request_key':uuid4().hex}).status_code==404

@pytest.mark.parametrize('path',['','/summary','/1'])
def test_inventory_is_private(client,path):
    r=client.get('/api/admin/inventory'+path)
    assert r.status_code==401 and r.headers['cache-control']=='private, no-store'
    assert client.patch('/api/admin/inventory/1',json={}).status_code==401

def test_inventory_filters_and_store_isolation(client):
    vid=sku(client);order(client,vid)
    with main.SessionLocal() as db: key=db.get(main.Variant,vid).sku
    data=client.get('/api/admin/inventory',headers=AUTH,params={'q':key,'availability':'reserved'}).json()
    assert data['total']==1 and data['items'][0]['reserved']==1
    assert client.get('/api/admin/inventory',headers=AUTH,params={'q':'%'}).json()['total']==0
    assert client.get('/api/admin/inventory/99999999',headers=AUTH).status_code==404
    summary=client.get('/api/admin/inventory/summary',headers=AUTH)
    assert summary.status_code==200,summary.text
    s=summary.json();assert s['on_hand']==s['available']+s['reserved']

@pytest.mark.parametrize('outcome',['cancelled','delivered'])
def test_old_writer_during_deploy_is_reconciled_without_replaying_stock(client,outcome):
    vid=sku(client);o,_=order(client,vid,2)
    with main.SessionLocal.begin() as db:
        if outcome=='cancelled':
            db.get(main.Order,o['id']).status='cancelled'
            db.get(main.Variant,vid).stock+=2
        else:
            db.add(main.OrderOperations(order_id=o['id'],delivery_status='delivered'))
    for _ in range(2):
        assert client.post('/api/admin/crm/index-orders',headers=AUTH).status_code==200
    expected=5 if outcome=='cancelled' else 3
    data=balances(client,vid,(expected,0,expected))
    assert [m['event'] for m in data['movements']].count('legacy_reconciled')==1

def test_historical_index_does_not_touch_original_catalog(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'historical.db'))
    old=[t for t in main.Base.metadata.sorted_tables if t.name not in {'stock_reservations','stock_movements'}]
    main.Base.metadata.create_all(engine,tables=old)
    with Session(engine) as db:
        db.add(main.Store(id=1,slug='test',name='Test'));db.flush()
        db.add(main.Category(id=1,store_id=1,slug='test',name='Test'));db.flush()
        db.add(main.Product(id=1,store_id=1,category_id=1,slug='test',name='Test'));db.flush()
        db.add(main.Variant(id=1,store_id=1,product_id=1,sku='OLD',price='10.00',stock=12));db.flush()
        for i,(status,qty,delivery) in enumerate([('pending',2,None),('confirmed',3,None),('fulfilled',4,None),('cancelled',1,None),('confirmed',2,'handed_over')],1):
            o=main.Order(id=i,store_id=1,request_key=uuid4().hex,name='Old test',email='old@example.test',
                         phone='0501234567',delivery_address='Test only',total=qty*10,status=status)
            db.add(o);db.flush()
            db.add(main.OrderItem(order_id=i,variant_id=1,sku='OLD',name='Test',quantity=qty,unit_price='10.00'))
            if delivery: db.add(main.OrderOperations(order_id=i,delivery_status=delivery))
        db.commit()
    main.Base.metadata.create_all(engine)
    for _ in range(2):
        with Session(engine) as db:
            main.inventory_backfill(db);db.commit()
            assert db.get(main.Variant,1).stock==12
            reservations=main.Base.metadata.tables['stock_reservations']
            assert db.scalar(select(func.sum(reservations.c.quantity)).where(reservations.c.state=='reserved'))==5
            assert db.scalar(select(func.sum(reservations.c.quantity)).where(reservations.c.state=='shipped'))==6
            assert db.scalar(select(func.count()).select_from(reservations))==5
    engine.dispose()
