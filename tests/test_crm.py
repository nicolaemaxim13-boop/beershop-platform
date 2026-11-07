"""CRM workflows use synthetic data and the real application transaction boundary."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, func
import app.main as main

AUTH = {'Authorization':'Bearer '+'t'*40}

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN','t'*40)
    with TestClient(main.app) as client:
        yield client

def place_order(client, email=None, phone='+380501234567', key=None):
    unique=uuid4().hex
    category=client.get('/api/categories').json()[0]['id']
    product=client.post('/api/admin/products',headers=AUTH,json={
        'name':'CRM fixture '+unique,'slug':'crm-'+unique,'category_id':category,
        'image_url':'/assets/crm-icons/store.svg'}).json()
    variant=client.post(f'/api/admin/products/{product["id"]}/variants',headers=AUTH,
                        json={'sku':'crm-'+unique,'price':'250.00','stock':5,'currency':'UAH'}).json()
    payload={'request_key':key or unique,'name':'CRM buyer '+unique,'email':email or unique+'@example.test',
             'phone':phone,'delivery_address':'Test branch 1',
             'items':[{'variant_id':variant['id'],'quantity':1}],'expected_total':'250.00'}
    response=client.post('/api/orders',json=payload)
    assert response.status_code==201,response.text
    return response.json(),payload

def detail(client,oid):
    response=client.get(f'/api/admin/crm/orders/{oid}',headers=AUTH)
    assert response.status_code==200,response.text
    return response.json()

@pytest.mark.parametrize('method,path,body',[
    ('get','/summary',None),('get','/orders',None),('get','/orders/1',None),
    ('get','/customers',None),('get','/customers/1',None),('get','/managers',None),
    ('post','/index-orders',{}),('patch','/orders/1',{'version':1}),
    ('post','/orders/1/notes',{'body':'private','request_key':'request-key'}),
    ('post','/customers/1/notes',{'body':'private','request_key':'request-key'})])
def test_crm_auth_and_cache_control(client,method,path,body):
    response=client.request(method,'/api/admin/crm'+path,json=body)
    assert response.status_code==401
    assert response.headers['cache-control']=='private, no-store'

def test_checkout_attaches_customer_atomically_and_idempotently(client):
    email=uuid4().hex+'@example.test'
    first,payload=place_order(client,email=email,phone='+38 (050) 123-45-67')
    second,_=place_order(client,email=email.upper(),phone='0501234567')
    a,b=detail(client,first['id']),detail(client,second['id'])
    assert a['customer_id']==b['customer_id']
    assert a['created_at'] and a['version']==1
    assert a['items'][0]['image_url']=='/assets/crm-icons/store.svg'
    assert client.post('/api/orders',json=payload).json()['id']==first['id']
    customer=client.get('/api/admin/crm/customers/'+str(a['customer_id']),headers=AUTH).json()
    assert len(customer['orders'])==2
    third,_=place_order(client,phone='0501234567')
    assert detail(client,third['id'])['customer_id']!=a['customer_id']

def test_assignment_versions_and_inactive_manager(client):
    order,_=place_order(client);oid=order['id']
    manager=client.post('/api/admin/crm/managers',headers=AUTH,json={'name':'Manager '+uuid4().hex}).json()
    body={'version':1,'manager_id':manager['id'],'priority':'high'}
    response=client.patch(f'/api/admin/crm/orders/{oid}',headers=AUTH,json=body)
    assert response.status_code==200,response.text
    assert response.json()['version']==2
    assert response.json()['manager_id']==manager['id']
    assert response.json()['audit'][0]['event']=='crm_assignment_updated'
    assert client.patch(f'/api/admin/crm/orders/{oid}',headers=AUTH,json=body).status_code==409
    client.patch(f'/api/admin/crm/managers/{manager["id"]}',headers=AUTH,json={'name':manager['name'],'active':False})
    assert client.patch(f'/api/admin/crm/orders/{oid}',headers=AUTH,json={**body,'version':2}).status_code==422
    assert client.patch(f'/api/admin/crm/orders/{oid}',headers=AUTH,json={'manager_id':None,'priority':'normal','version':2}).status_code==200

def test_notes_customer_edits_and_order_snapshot(client):
    order,_=place_order(client);oid=order['id'];customer=detail(client,oid)['customer']
    note={'body':'<script>alert(1)</script>\nInternal note','request_key':uuid4().hex}
    endpoint=f'/api/admin/crm/orders/{oid}/notes'
    first=client.post(endpoint,headers=AUTH,json=note)
    assert first.status_code==201,first.text
    assert client.post(endpoint,headers=AUTH,json=note).json()['id']==first.json()['id']
    assert client.post(endpoint,headers=AUTH,json={**note,'body':'changed'}).status_code==409
    assert client.post(endpoint,headers=AUTH,json={'body':'   ','request_key':uuid4().hex}).status_code==422
    cid=customer['id']
    body={'name':'Corrected contact','email':customer['email'],'phone':customer['phone'],'version':1}
    result=client.patch(f'/api/admin/crm/customers/{cid}',headers=AUTH,json=body)
    assert result.status_code==200,result.text
    assert result.json()['version']==2
    assert result.json()['orders'][0]['name']==order['name']
    assert result.json()['notes'][1]['body']==note['body']
    assert client.patch(f'/api/admin/crm/customers/{cid}',headers=AUTH,json=body).status_code==409
    assert detail(client,oid)['name']==order['name']

def test_customer_collision_does_not_merge_history(client):
    a,_=place_order(client);b,_=place_order(client)
    ca=detail(client,a['id'])['customer'];cb=detail(client,b['id'])['customer']
    response=client.patch(f'/api/admin/crm/customers/{cb["id"]}',headers=AUTH,
                          json={'name':ca['name'],'email':ca['email'],'phone':ca['phone'],'version':1})
    assert response.status_code==409
    assert detail(client,b['id'])['customer_id']==cb['id']
    assert client.post('/api/admin/crm/customers',headers=AUTH,json={k:ca[k] for k in ['name','email','phone']}).status_code==409

def test_search_pagination_and_filters(client):
    email=uuid4().hex+'@example.test'
    one,_=place_order(client,email=email);two,_=place_order(client,email=email)
    client.patch(f'/api/admin/orders/{one["id"]}/status',headers=AUTH,json={'status':'confirmed'})
    response=client.get('/api/admin/crm/orders',headers=AUTH,params={'q':email,'limit':1,'page':2}).json()
    assert response['total']==2 and response['items'][0]['id']==one['id']
    filtered=client.get('/api/admin/crm/orders',headers=AUTH,params={'q':email,'status':'confirmed'}).json()
    assert filtered['total']==1 and filtered['items'][0]['id']==one['id']
    assert client.get('/api/admin/crm/orders',headers=AUTH,params={'q':'%'}).json()['total']==0
    assert client.get('/api/admin/crm/orders',headers=AUTH,params={'q':f'BS-{two["id"]:06}'}).json()['items'][0]['id']==two['id']
    assert client.get('/api/admin/crm/orders?manager=bogus',headers=AUTH).status_code==422
    assert client.get('/api/admin/crm/orders?limit=101',headers=AUTH).status_code==422
    assert client.get('/api/admin/crm/orders',headers=AUTH,params={'q':'9'*100}).json()['total']==0
    assert client.get('/api/admin/crm/customers?page=999999999999999999999',headers=AUTH).status_code==422

def test_crm_cannot_skip_payment_or_delivery(client):
    order,_=place_order(client);oid=order['id'];vid=order['items'][0]['variant_id']
    assert client.patch(f'/api/admin/crm/orders/{oid}',headers=AUTH,json={'version':1,'payment_status':'paid'}).status_code==422
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'confirmed'}).status_code==200
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'fulfilled'}).status_code==409
    for _ in range(2):
        assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'cancelled'}).status_code==200
    with main.SessionLocal() as db:
        assert db.get(main.Variant,vid).stock==5
        assert db.get(main.Order,oid).payment_status=='unpaid'
    events=detail(client,oid)['audit']
    assert sum(a['event']=='order_status_changed' for a in events)==2

def test_failed_customer_attachment_rolls_back_checkout(client,monkeypatch):
    order,payload=place_order(client)
    vid=order['items'][0]['variant_id'];payload['request_key']=uuid4().hex
    payload['email']=uuid4().hex+'@example.test'
    original=main.crm_attach_order
    def fail(db,order):
        original(db,order)
        raise HTTPException(503,'Synthetic failure')
    monkeypatch.setattr(main,'crm_attach_order',fail)
    assert client.post('/api/orders',json=payload).status_code==503
    with main.SessionLocal() as db:
        assert db.get(main.Variant,vid).stock==4
        assert db.scalar(select(main.Order).where(main.Order.request_key==payload['request_key'])) is None
        customers=main.Base.metadata.tables['crm_customers']
        assert db.scalar(select(func.count()).select_from(customers).where(customers.c.email==payload['email']))==0

@pytest.mark.parametrize('paid,tracking',[(True,None),(False,'20400000000001')])
def test_cancellation_requires_separate_return_flow(client,paid,tracking):
    order,_=place_order(client);oid=order['id'];vid=order['items'][0]['variant_id']
    with main.SessionLocal.begin() as db:
        db.get(main.Order,oid).payment_status='paid' if paid else 'unpaid'
        if tracking: db.add(main.OrderOperations(order_id=oid,tracking_number=tracking,delivery_status='handed_over'))
    assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,json={'status':'cancelled'}).status_code==409
    with main.SessionLocal() as db:
        assert db.get(main.Variant,vid).stock==4
        assert db.get(main.Order,oid).status=='pending'

def test_backfill_is_repeatable_and_preserves_history(client):
    with main.SessionLocal.begin() as db:
        order=main.Order(store_id=1,request_key=uuid4().hex,name='Historical',email=uuid4().hex+'@example.test',
                         phone='0501234567',delivery_address='Historical address',total='123.45',status='cancelled')
        db.add(order);db.flush();oid=order.id
    for _ in range(2):
        assert client.post('/api/admin/crm/index-orders',headers=AUTH).status_code==200
    data=detail(client,oid)
    assert data['created_at'] is None
    assert data['status']=='cancelled' and data['total']=='123.45'
    assert len(client.get('/api/admin/crm/customers/'+str(data['customer_id']),headers=AUTH).json()['orders'])==1

def test_crm_store_scope(client):
    with main.SessionLocal.begin() as db:
        if not db.get(main.Store,2): db.add(main.Store(id=2,slug='isolated',name='Other store'));db.flush()
        order=main.Order(store_id=2,request_key=uuid4().hex,name='Other store',email=uuid4().hex+'@example.test',
                         phone='0501234567',delivery_address='Other',total='10.00')
        db.add(order);db.flush();main.crm_attach_order(db,order);oid=order.id
        details=main.Base.metadata.tables['crm_order_details']
        cid=db.scalar(select(details.c.customer_id).where(details.c.order_id==oid))
    assert client.get(f'/api/admin/crm/orders/{oid}',headers=AUTH).status_code==404
    assert client.get(f'/api/admin/crm/customers/{cid}',headers=AUTH).status_code==404
    assert client.post(f'/api/admin/crm/orders/{oid}/notes',headers=AUTH,json={'body':'No','request_key':uuid4().hex}).status_code==404

def test_crm_assets_and_entry(client):
    response=client.get('/crm')
    assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
    assert 'BeerShop CRM' in response.text
    assert 'id="root"' in response.text
    assert '/crm/assets/' in response.text
    assert client.get('/crm/').status_code==200
    legacy=client.get('/crm-legacy')
    assert legacy.status_code==200 and 'id="login-form"' in legacy.text
    assert legacy.headers['cache-control']=='private, no-store'
    import re
    for asset in re.findall(r'(?:src|href)="(/crm/assets/[^"]+)"',response.text):
        bundle=client.get(asset)
        assert bundle.status_code==200
        assert bundle.headers['x-content-type-options']=='nosniff'
    assert client.get('/crm/assets/does-not-exist.js').status_code==404
    assert 'href="/crm"' in client.get('/manage').text
    for path in ['/assets/crm.js','/assets/crm.css','/assets/crm-icons/store.svg']:
        assert client.get(path).status_code==200
    assert client.get('/assets/crm-icons/secret.txt').status_code==404
