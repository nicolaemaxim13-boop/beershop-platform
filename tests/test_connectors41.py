"""Provider integration 4.1: no real outbound network calls in CI."""
import os
from uuid import uuid4
from decimal import Decimal
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
import app.connectors41 as connector

AUTH={'Authorization':'Bearer '+'t'*40}

def create_order(client):
    cid=client.get('/api/categories').json()[0]['id']
    slug='cn-'+uuid4().hex[:11]
    p=client.post('/api/admin/products',headers=AUTH,json={
        'category_id':cid,'slug':slug,'name':'Connector Test Product'})
    assert p.status_code==201,p.text
    variant=client.post('/api/admin/products/'+str(p.json()['id'])+'/variants',
                        headers=AUTH,json={'sku':slug+'-A','price':'120.00','stock':3,'currency':'UAH'})
    assert variant.status_code==201,variant.text
    items=[{'variant_id':variant.json()['id'],'quantity':1}]
    q=client.post('/api/quote',json={'items':items})
    assert q.status_code==200,q.text
    r=client.post('/api/orders',json={
        'request_key':uuid4().hex,'name':'Example Customer','email':'buyer@example.org',
        'phone':'+380501231234','delivery_address':'Kyiv branch 1',
        'items':items,'expected_total':q.json()['total']})
    assert r.status_code==201,r.text
    return r.json()

def test_connectors_are_off_by_default(monkeypatch):
    monkeypatch.delenv('KEYCRM_API_KEY',raising=False)
    monkeypatch.delenv('KEYCRM_SYNC_ENABLED',raising=False)
    monkeypatch.delenv('NOVA_POSHTA_API_KEY',raising=False)
    with TestClient(app) as c:
        assert c.get('/api/admin/connectors41/status').status_code==401
        status=c.get('/api/admin/connectors41/status',headers=AUTH)
        assert status.status_code==200,status.text
        data=status.json()
        assert data['keycrm']=={'configured':False,'outbound_enabled':False}
        assert data['privatbank']['configured'] is False
        assert data['payment_automation'] is False
        assert c.get('/api/admin/connectors41/keycrm/sources',headers=AUTH).status_code==503
        assert c.get('/assets/connectors41.js').status_code==200
        assert 'Інтеграції 4.1' in c.get('/manage').text

def test_keycrm_one_shot_dispatch_and_reconciliation(monkeypatch):
    monkeypatch.setenv('KEYCRM_API_KEY','MOCK-KEYCRM-API-KEY')
    monkeypatch.setenv('KEYCRM_SYNC_ENABLED','1')
    calls=[]
    monkeypatch.setattr(connector,'keycrm_sources',lambda:[{'id':36,'name':'BeerShop'}])
    def fake_send(payload):
        calls.append(payload)
        return {'id':78654}
    monkeypatch.setattr(connector,'send_keycrm_order',fake_send)
    with TestClient(app) as c:
        order=create_order(c)
        oid=order['id']
        assert c.get('/api/admin/connectors41/keycrm/sources',headers=AUTH).json()=={
            'sources':[{'id':36,'name':'BeerShop'}]}
        uri=f'/api/admin/connectors41/keycrm/sync/{oid}'
        body={'source_id':36,'attestation':'SEND_PERSONAL_DATA_TO_KEYCRM'}
        assert c.post(uri,json=body).status_code==401
        result=c.post(uri,headers=AUTH,json=body)
        assert result.status_code==200,result.text
        assert result.json()['state']=='confirmed'
        assert result.json()['external_id']=='78654'
        assert calls[0]['source_uuid']==f'BS-{oid:06}'
        assert calls[0]['products'][0]['sku'].startswith('cn-')
        assert calls[0]['products'][0]['price']==order['items'][0]['unit_price']
        assert len(calls)==1
        assert c.post(uri,headers=AUTH,json=body).json()['state']=='already_linked'
        assert len(calls)==1
        state=c.get('/api/admin/connectors41/keycrm/dispatch/'+str(oid),headers=AUTH).json()
        assert state['state']=='confirmed'
        assert state['external_id']=='78654'
        assert c.get('/api/admin/ops/orders',headers=AUTH).json()[0]['crm_order_id']=='78654'

        # A timed-out POST might have succeeded remotely. Never retry it automatically.
        other=create_order(c)
        def unknown(payload):
            calls.append(payload)
            raise HTTPException(502,'provider outcome unknown')
        monkeypatch.setattr(connector,'send_keycrm_order',unknown)
        endpoint=f'/api/admin/connectors41/keycrm/sync/{other["id"]}'
        failure=c.post(endpoint,headers=AUTH,json=body)
        assert failure.status_code==502
        assert c.post(endpoint,headers=AUTH,json=body).status_code==409
        assert len(calls)==2
        pending=c.get('/api/admin/connectors41/keycrm/dispatch/'+str(other['id']),
                      headers=AUTH).json()
        assert pending['state']=='needs_reconciliation'
        manual=c.post(f'/api/admin/ops/orders/{other["id"]}/keycrm-confirm',headers=AUTH,
                      json={'external_order_id':'CRM-UNKNOWN-1234',
                            'attestation':'ORDER_CONFIRMED_IN_KEYCRM'})
        assert manual.status_code==200,manual.text
        done=c.get('/api/admin/connectors41/keycrm/dispatch/'+str(other['id']),
                   headers=AUTH).json()
        assert done['state']=='reconciled'
        assert done['external_id']=='CRM-UNKNOWN-1234'

def test_keycrm_requires_explicit_feature_enable(monkeypatch):
    monkeypatch.setenv('KEYCRM_API_KEY','MOCK-KEY')
    monkeypatch.delenv('KEYCRM_SYNC_ENABLED',raising=False)
    with TestClient(app) as c:
        result=c.post('/api/admin/connectors41/keycrm/sync/1',headers=AUTH,
                      json={'source_id':36,'attestation':'SEND_PERSONAL_DATA_TO_KEYCRM'})
        assert result.status_code==503

def test_nova_tracking_read_only_and_key_guard(monkeypatch):
    monkeypatch.delenv('NOVA_POSHTA_API_KEY',raising=False)
    with TestClient(app) as c:
        assert c.get('/api/admin/connectors41/nova-poshta/tracking/1').status_code==401
        assert c.get('/api/admin/connectors41/nova-poshta/tracking/999999',headers=AUTH).status_code==404
        order=create_order(c)
        oid=order['id']
        assert c.get(f'/api/admin/connectors41/nova-poshta/tracking/{oid}',headers=AUTH).status_code==409
        tx={'transaction_id':'TX-'+uuid4().hex,'amount':order['total'],'currency':'UAH',
            'reference':f'BS-{oid:06}', 'attestation':'BANK_STATEMENT_CHECKED'}
        assert c.post(f'/api/admin/ops/orders/{oid}/bank-confirm',headers=AUTH,
                      json=tx).status_code==200
        tt='20450000000019'
        way=c.post(f'/api/admin/ops/orders/{oid}/nova-poshta/waybill',headers=AUTH,
                   json={'tracking_number':tt,'attestation':'WAYBILL_CHECKED_IN_NOVA_POSHTA'})
        assert way.status_code==200,way.text
        missing=c.get(f'/api/admin/connectors41/nova-poshta/tracking/{oid}',headers=AUTH)
        assert missing.status_code==503
        def simulated_track(n):
            assert n==tt
            return {'tracking_number':n,'status':'Отримано відправником','status_code':'1',
                    'source':'Nova Poshta'}
        monkeypatch.setattr(connector,'track_nova_poshta',simulated_track)
        live=c.get(f'/api/admin/connectors41/nova-poshta/tracking/{oid}',headers=AUTH)
        assert live.status_code==200,live.text
        assert live.json()['tracking_number']==tt
        # Reading actual courier status is never sufficient to update a local order.
        local=c.get('/api/admin/ops/orders',headers=AUTH).json()[0]
        assert local['delivery_status']=='created'


def test_provider_request_has_no_redirect(monkeypatch):
    # Validate callback semantics without internet.
    opener=connector.build_opener(connector._NoRedirect())
    import urllib.request
    from urllib.response import addinfourl
    assert connector._NoRedirect().redirect_request(None,None,302,'Moved',{},'https://evil.example') is None
    assert connector.KEYCRM_ORIGIN=='https://openapi.keycrm.app/v1'
    assert connector.NP_ENDPOINT=='https://api.novaposhta.ua/v2.0/json/'
