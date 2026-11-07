"""BeerShop 4.0 operations safety contract; no external provider calls."""
import os
from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app

os.environ['BEERSHOP_ADMIN_TOKEN']='t'*40
AUTH={'Authorization':'Bearer '+'t'*40}

def make_order(client, variant_id):
    data={'request_key':uuid4().hex,
          'name':'Delivery Test','email':'test@example.org',
          'phone':'+380661234567','delivery_address':'Kyiv test branch 42',
          'items':[{'variant_id':variant_id,'quantity':1}]}
    quote=client.post('/api/quote',json={'items':data['items']})
    assert quote.status_code==200, quote.text
    data['expected_total']=quote.json()['total']
    res=client.post('/api/orders',json=data)
    assert res.status_code==201, res.text
    return res.json()

def test_manual_operations_have_audit_idempotency_and_guards():
    with TestClient(app) as client:
        assert client.get('/api/admin/ops/summary').status_code==401
        assert client.get('/api/admin/ops/orders').status_code==401
        cats=client.get('/api/categories').json()
        slug='ops-'+uuid4().hex[:12]
        p=client.post('/api/admin/products',headers=AUTH,json={
            'category_id':cats[0]['id'],'slug':slug,'name':'Test Cooler'})
        assert p.status_code==201, p.text
        v=client.post('/api/admin/products/'+str(p.json()['id'])+'/variants',headers=AUTH,
                      json={'sku':slug,'price':'250.00','currency':'UAH','stock':4})
        assert v.status_code==201,v.text
        vid=v.json()['id']
        a=make_order(client,vid)
        b=make_order(client,vid)
        oid=a['id']
        assert client.patch(f'/api/admin/orders/{oid}/payment',headers=AUTH,
                            json={'payment_status':'paid'}).status_code==409
        assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,
                            json={'status':'fulfilled'}).status_code==409
        assert client.post(f'/api/admin/ops/orders/{oid}/bank-confirm',
                           json={}).status_code==401
        base={'transaction_id':'BANK-'+uuid4().hex,
              'amount':a['total'],'currency':'UAH',
              'reference':f'Оплата BS-{oid:06}','attestation':'BANK_STATEMENT_CHECKED'}
        wrong=client.post(f'/api/admin/ops/orders/{oid}/bank-confirm',headers=AUTH,
                          json={**base,'amount':'1.00'})
        assert wrong.status_code==409
        wrong_ref=client.post(f'/api/admin/ops/orders/{oid}/bank-confirm',headers=AUTH,
                              json={**base,'reference':'неправильне призначення'})
        assert wrong_ref.status_code==409
        paid=client.post(f'/api/admin/ops/orders/{oid}/bank-confirm',
                         headers=AUTH,json=base)
        assert paid.status_code==200,paid.text
        assert paid.json()['bank_confirmed'] is True
        repeat=client.post(f'/api/admin/ops/orders/{oid}/bank-confirm',
                           headers=AUTH,json=base)
        assert repeat.status_code==200
        assert client.post(f'/api/admin/ops/orders/{b["id"]}/bank-confirm',
                           headers=AUTH,json={**base,
                           'reference':f'Оплата BS-{b["id"]:06}',
                           'amount':b['total']}).status_code==409
        assert client.post(f'/api/admin/ops/orders/{b["id"]}/nova-poshta/waybill',
                           headers=AUTH,json={'tracking_number':'20450000000001',
                           'attestation':'WAYBILL_CHECKED_IN_NOVA_POSHTA'}).status_code==409
        missing=client.get(f'/api/admin/ops/orders/{oid}/keycrm-payload',headers=AUTH)
        assert missing.status_code==422
        draft=client.get(f'/api/admin/ops/orders/{oid}/keycrm-payload?source_id=123',
                         headers=AUTH)
        assert draft.status_code==200,draft.text
        assert draft.json()['source_uuid']==f'BS-{oid:06}'
        assert draft.json()['products'][0]['quantity']==1
        assert client.get(f'/api/admin/ops/orders/{oid}/keycrm-payload?source_id=123',headers=AUTH).json()==draft.json()
        crm={'external_order_id':'CRM-'+uuid4().hex,'attestation':'ORDER_CONFIRMED_IN_KEYCRM'}
        assert client.post(f'/api/admin/ops/orders/{oid}/keycrm-confirm',
                           headers=AUTH,json=crm).status_code==200
        assert client.post(f'/api/admin/ops/orders/{oid}/keycrm-confirm',
                           headers=AUTH,json=crm).status_code==200
        assert client.post(f'/api/admin/ops/orders/{oid}/keycrm-confirm',
                           headers=AUTH,json={**crm,'external_order_id':'OTHER-123'}).status_code==409
        way={'tracking_number':'20450000000001','attestation':'WAYBILL_CHECKED_IN_NOVA_POSHTA'}
        assert client.post(f'/api/admin/ops/orders/{oid}/nova-poshta/waybill',
                           headers=AUTH,json=way).status_code==200
        assert client.post(f'/api/admin/ops/orders/{oid}/nova-poshta/waybill',
                           headers=AUTH,json=way).status_code==200
        assert client.patch(f'/api/admin/ops/orders/{oid}/nova-poshta/status',
                            headers=AUTH,json={'status':'delivered',
                            'attestation':'STATUS_CHECKED_IN_NOVA_POSHTA'}).status_code==409
        assert client.patch(f'/api/admin/ops/orders/{oid}/nova-poshta/status',
                            headers=AUTH,json={'status':'handed_over',
                            'attestation':'STATUS_CHECKED_IN_NOVA_POSHTA'}).status_code==200
        assert client.patch(f'/api/admin/ops/orders/{oid}/nova-poshta/status',
                            headers=AUTH,json={'status':'delivered',
                            'attestation':'STATUS_CHECKED_IN_NOVA_POSHTA'}).status_code==200
        assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,
                            json={'status':'confirmed'}).status_code==200
        assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,
                            json={'status':'picking'}).status_code==200
        assert client.patch(f'/api/admin/orders/{oid}/status',headers=AUTH,
                            json={'status':'fulfilled'}).status_code==200
        trail=client.get(f'/api/admin/ops/orders/{oid}/audit',headers=AUTH)
        assert trail.status_code==200 and len(trail.json())>=5
        ops=client.get('/api/admin/ops/orders',headers=AUTH)
        assert ops.status_code==200
        assert any(o['id']==oid and o['crm_synced'] and o['bank_confirmed'] for o in ops.json())
        assert client.get('/assets/ops40.js').status_code==200
        assert 'Операції 4.0' in client.get('/manage').text

