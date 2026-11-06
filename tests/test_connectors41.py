"""Provider integration 4.1: no real outbound network calls in CI."""
import os
import io
import json
import pytest
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
    import urllib.request
    from urllib.response import addinfourl
    assert connector._NoRedirect().redirect_request(None,None,302,'Moved',{},'https://evil.example') is None
    assert connector.KEYCRM_ORIGIN=='https://openapi.keycrm.app/v1'
    assert connector.NP_ENDPOINT=='https://api.novaposhta.ua/v2.0/json/'


def test_crm_refresh_reconciles_and_never_confirms_payment(monkeypatch):
    monkeypatch.setenv('KEYCRM_API_KEY', 'TEST-ONLY')
    monkeypatch.setenv('KEYCRM_SYNC_ENABLED', '1')
    monkeypatch.setattr(connector, 'send_keycrm_order', lambda payload: (_ for _ in ()).throw(HTTPException(502)))
    with TestClient(app) as c:
        order = create_order(c)
        oid = order['id']
        assert c.post(f'/api/admin/connectors41/keycrm/sync/{oid}', headers=AUTH,
                      json={'source_id':36,'attestation':'SEND_PERSONAL_DATA_TO_KEYCRM'}).status_code == 502
        response = {'id':987654,'source_id':36,'source_uuid':f'BS-{oid:06}',
                    'status_id':7,'status':{'id':7,'name':'Complete'},'payment_status':'paid',
                    'buyer':{'email':'must-not-be-stored@example.invalid'}}
        monkeypatch.setattr(connector, 'read_keycrm_order', lambda external: response)
        endpoint = f'/api/admin/connectors41/keycrm/refresh/{oid}'
        body = {'source_id':36,'external_order_id':'987654','attestation':'CHECK_ORDER_IN_KEYCRM'}
        assert c.post(endpoint, json=body).status_code == 401
        assert c.post(endpoint, headers=AUTH, json={**body,'attestation':'no'}).status_code == 422
        result = c.post(endpoint, headers=AUTH, json=body)
        assert result.status_code == 200, result.text
        assert result.json()['status_id'] == 7
        snapshot = c.get(f'/api/admin/connectors41/snapshot/{oid}', headers=AUTH).json()
        assert snapshot['keycrm']['external_id'] == '987654'
        assert 'buyer' not in json.dumps(snapshot)
        dispatch = c.get(f'/api/admin/connectors41/keycrm/dispatch/{oid}', headers=AUTH).json()
        assert dispatch['state'] == 'reconciled'
        assert c.post(endpoint, headers=AUTH, json=body).status_code == 200
        local = c.get('/api/admin/ops/orders', headers=AUTH).json()[0]
        assert local['payment_status'] == 'unpaid'
        assert local['order_status'] == 'pending'


@pytest.mark.parametrize('change,expected', [
    ({'id':123},409), ({'source_uuid':'BS-OTHER'},409), ({'source_id':99},409),
    ({'id':True},409), ({'status_id':None},502), ({'status':{'id':9,'name':'Bad'}},502)])
def test_crm_mismatches_leave_order_unlinked(monkeypatch, change, expected):
    with TestClient(app) as c:
        oid = create_order(c)['id']
        data = {'id':987655,'source_id':36,'source_uuid':f'BS-{oid:06}',
                'status_id':7,'status':{'id':7,'name':'Complete'},**change}
        monkeypatch.setattr(connector, 'read_keycrm_order', lambda external: data)
        result = c.post(f'/api/admin/connectors41/keycrm/refresh/{oid}', headers=AUTH,
                        json={'source_id':36,'external_order_id':'987655','attestation':'CHECK_ORDER_IN_KEYCRM'})
        assert result.status_code == expected, result.text
        assert c.get('/api/admin/ops/orders', headers=AUTH).json()[0]['crm_order_id'] is None


def test_delivery_sync_is_monotonic_audited_and_payment_independent(monkeypatch):
    from app.main import SessionLocal, OrderOperations
    with TestClient(app) as c:
        oid = create_order(c)['id']
        number = str(20450000000000 + oid)
        with SessionLocal.begin() as db:
            db.add(OrderOperations(order_id=oid,tracking_number=number,delivery_status='created'))
        endpoint = f'/api/admin/connectors41/nova-poshta/sync/{oid}'
        body = {'attestation':'SYNC_EXISTING_WAYBILL_STATUS'}
        assert c.post(endpoint,json=body).status_code == 401
        for code, expected, review in [('5','handed_over',False),('9','delivered',False),
                                      ('9','delivered',False),('1','delivered',True),('999','delivered',True)]:
            monkeypatch.setattr(connector, 'track_nova_poshta', lambda n: {
                'tracking_number':n,'status_code':code,'status':'Test status'})
            result = c.post(endpoint,headers=AUTH,json=body)
            assert result.status_code == 200,result.text
            assert result.json()['delivery_status'] == expected
            assert result.json()['review_required'] is review
        local = c.get('/api/admin/ops/orders',headers=AUTH).json()[0]
        assert local['payment_status'] == 'unpaid'
        assert local['order_status'] == 'pending'
        snapshot = c.get(f'/api/admin/connectors41/snapshot/{oid}',headers=AUTH).json()
        assert snapshot['nova_poshta']['status_code'] == '999'


def mock_transport(monkeypatch, data, status=200):
    calls = []
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            response = Response(data if isinstance(data, bytes) else json.dumps(data).encode())
            response.status = status
            return response
    monkeypatch.setattr(connector, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(connector, '_LAST_CALL', [-100.0])
    return calls


def test_official_transport_contracts_and_redaction(monkeypatch):
    monkeypatch.setenv('KEYCRM_API_KEY','TEST-CRM-SECRET')
    calls = mock_transport(monkeypatch, {'id':123})
    assert connector.read_keycrm_order('123') == {'id':123}
    assert calls[0].full_url == 'https://openapi.keycrm.app/v1/order/123?include=status'
    assert calls[0].get_method() == 'GET'
    assert calls[0].get_header('Authorization') == 'Bearer TEST-CRM-SECRET'
    with pytest.raises(HTTPException) as error:
        connector.read_keycrm_order('../other')
    assert error.value.status_code == 422
    monkeypatch.setenv('NOVA_POSHTA_API_KEY','TEST-NP-SECRET')
    number = '20450000000001'
    calls = mock_transport(monkeypatch, {'success':True,'data':[{
        'Number':number,'StatusCode':'9','Status':'Received','RecipientFullName':'PRIVATE'}]})
    result = connector.track_nova_poshta(number)
    payload = json.loads(calls[0].data)
    assert calls[0].full_url == connector.NP_ENDPOINT
    assert payload['modelName'] == 'TrackingDocument'
    assert payload['calledMethod'] == 'getStatusDocuments'
    assert payload['methodProperties'] == {'Documents':[{'DocumentNumber':number}]}
    assert 'PRIVATE' not in json.dumps(result)
    assert 'TEST-NP-SECRET' not in json.dumps(result)


@pytest.mark.parametrize('data', [
    {'success':False,'errors':['TEST-NP-SECRET']},
    {'success':True,'data':[]},
    {'success':True,'data':[{'Number':'20450000000002'}]},
    b'not json', b'x'*131073])
def test_invalid_provider_responses_are_safe(monkeypatch, data):
    monkeypatch.setenv('NOVA_POSHTA_API_KEY','TEST-NP-SECRET')
    mock_transport(monkeypatch, data)
    with pytest.raises(HTTPException) as error:
        connector.track_nova_poshta('20450000000001')
    assert error.value.status_code == 502
    assert 'TEST-NP-SECRET' not in error.value.detail


def test_privatbank_availability_never_reads_transactions(monkeypatch):
    monkeypatch.setenv('PRIVATBANK_STATEMENT_TOKEN','TEST-BANK-SECRET')
    monkeypatch.delenv('PRIVATBANK_READ_ENABLED',raising=False)
    with TestClient(app) as c:
        endpoint = '/api/admin/connectors41/privatbank/availability'
        assert c.get(endpoint).status_code == 401
        assert c.get(endpoint,headers=AUTH).status_code == 503
        monkeypatch.setenv('PRIVATBANK_READ_ENABLED','1')
        calls = mock_transport(monkeypatch, {'status':'SUCCESS','type':'settings',
                                            'settings':{'phase':'WRK','work_balance':'N'}})
        result = c.get(endpoint,headers=AUTH)
        assert result.status_code == 200,result.text
        assert result.json()['settings_access_verified'] is True
        assert result.json()['statements_access_verified'] is False
        assert result.json()['payment_automation'] is False
        assert calls[0].full_url == connector.PRIVATBANK_SETTINGS
        assert calls[0].get_method() == 'GET'
        assert calls[0].get_header('Token') == 'TEST-BANK-SECRET'
        assert 'TEST-BANK-SECRET' not in result.text


def test_bank_reference_requires_exact_invoice(monkeypatch):
    with TestClient(app) as c:
        order = create_order(c)
        oid = order['id']
        body = {'transaction_id':'TEST-'+uuid4().hex,'amount':order['total'],'currency':'UAH',
                'reference':f'BS-{oid:06}0','attestation':'BANK_STATEMENT_CHECKED'}
        response = c.post(f'/api/admin/ops/orders/{oid}/bank-confirm',headers=AUTH,json=body)
        assert response.status_code == 409
        assert c.get('/api/admin/ops/orders',headers=AUTH).json()[0]['payment_status'] == 'unpaid'


@pytest.mark.parametrize('failure,expected', [(401,503),(403,503),(429,503),(500,502),('timeout',502)])
def test_transport_does_not_retry_or_expose_provider_errors(monkeypatch, failure, expected):
    from urllib.error import HTTPError
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if failure == 'timeout':
                raise TimeoutError('SECRET-AND-BUYER')
            raise HTTPError(request.full_url,failure,'SECRET-AND-BUYER',{},io.BytesIO(b'SECRET-AND-BUYER'))
    monkeypatch.setattr(connector,'build_opener',lambda *args:Opener())
    with pytest.raises(HTTPException) as error:
        connector._request_json(connector.KEYCRM_ORIGIN+'/order','POST',payload={'test':True})
    assert error.value.status_code == expected
    assert 'SECRET-AND-BUYER' not in error.value.detail
    assert len(calls) == 1


def test_bank_availability_rejects_invalid_or_busy_response(monkeypatch):
    monkeypatch.setenv('PRIVATBANK_STATEMENT_TOKEN','TEST-ONLY')
    monkeypatch.setenv('PRIVATBANK_READ_ENABLED','1')
    mock_transport(monkeypatch,{'status':'ERROR','description':'TEST-ONLY'})
    with pytest.raises(HTTPException) as error:
        connector.privatbank_availability()
    assert error.value.status_code == 502
    mock_transport(monkeypatch,{'status':'SUCCESS','type':'settings','settings':{'phase':'REG','work_balance':'Y'}})
    assert connector.privatbank_availability()['statements_available_now'] is False

