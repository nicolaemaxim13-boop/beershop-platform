"""USD-anchored pricing regression tests; no network calls to NBU."""
import os
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app, FxState, SessionLocal
from app.fx import convert_price

os.environ['BEERSHOP_ADMIN_TOKEN']='t'*40
AUTH={'Authorization':'Bearer '+'t'*40}

def test_fx_math():
    assert str(convert_price('10.00','USD','40','40'))=='400.00'
    assert str(convert_price('100.00','UAH','44','40'))=='110.00'
    assert str(convert_price('19.995','USD','40','40'))=='799.80'
    assert str(convert_price('0.01','USD','40','40'))=='0.40'

def test_fx_catalog_quotes_and_orders():
    with TestClient(app) as c:
        with SessionLocal.begin() as db:
            state=db.get(FxState,1)
            if state is None:
                state=FxState(id=1,current_rate=Decimal('40'),baseline_rate=Decimal('40'),
                              effective_date=datetime.now(timezone.utc).date().isoformat(),checked_at=datetime.now(timezone.utc).isoformat())
                db.add(state)
            else:
                state.current_rate=Decimal('40')
                state.baseline_rate=Decimal('40')
                state.checked_at=datetime.now(timezone.utc).isoformat()
        cid=c.get('/api/categories').json()[0]['id']
        us='fx-usd-'+uuid4().hex[:8]
        uah='fx-uah-'+uuid4().hex[:8]
        up=c.post('/api/admin/products',headers=AUTH,json={'category_id':cid,'slug':us,'name':'USD base product'})
        hp=c.post('/api/admin/products',headers=AUTH,json={'category_id':cid,'slug':uah,'name':'UAH base product'})
        assert up.status_code==201 and hp.status_code==201
        a=c.post('/api/admin/products/'+str(up.json()['id'])+'/variants',headers=AUTH,
                 json={'sku':us,'price':'10.00','currency':'USD','stock':4})
        b=c.post('/api/admin/products/'+str(hp.json()['id'])+'/variants',headers=AUTH,
                 json={'sku':uah,'price':'100.00','currency':'UAH','stock':4})
        assert a.status_code==201 and b.status_code==201
        aid,bid=a.json()['id'],b.json()['id']
        first=c.get('/api/products/'+us).json()['variants'][0]
        assert first['price']=='400.00' and first['currency']=='UAH'
        assert first['source_price']=='10.00' and first['source_currency']=='USD'
        assert c.get('/api/products/'+uah).json()['variants'][0]['price']=='100.00'
        with SessionLocal.begin() as db:
            state=db.get(FxState,1)
            state.current_rate=Decimal('44')
            state.checked_at=datetime.now(timezone.utc).isoformat()
        assert c.get('/api/products/'+us).json()['variants'][0]['price']=='440.00'
        assert c.get('/api/products/'+uah).json()['variants'][0]['price']=='110.00'
        q=c.post('/api/quote',json={'items':[{'variant_id':aid,'quantity':1},
                                  {'variant_id':bid,'quantity':2}]})
        assert q.status_code==200 and q.json()['total']=='660.00'
        payload={'request_key':uuid4().hex,'name':'Quote Customer','email':'test@example.com',
                 'phone':'+380123456789','delivery_address':'Kyiv test office',
                 'items':[{'variant_id':aid,'quantity':1},{'variant_id':bid,'quantity':2}],
                 'expected_total':'660.00'}
        assert c.post('/api/orders',json={**payload,'expected_total':'600.00'}).status_code==409
        order=c.post('/api/orders',json=payload)
        assert order.status_code==201,order.text
        assert order.json()['total']=='660.00'
        assert sorted(x['unit_price'] for x in order.json()['items'])==['110.00','440.00']
        with SessionLocal.begin() as db:
            state=db.get(FxState,1)
            state.current_rate=Decimal('45')
            state.checked_at=datetime.now(timezone.utc).isoformat()
        assert c.post('/api/orders',json=payload).json()['total']=='660.00'
        assert c.get('/assets/store-fx.js').status_code==200
        assert 'id="fx-info"' in c.get('/').text

def test_fx_outage_blocks_stale_rates(monkeypatch):
    import app.main as main
    with TestClient(app) as c:
        with SessionLocal.begin() as db:
            state=db.get(FxState,1)
            if state is not None:
                state.checked_at=(datetime.now(timezone.utc)-timedelta(days=6)).isoformat()
        def offline():
            raise OSError('NBU network unavailable')
        monkeypatch.setattr(main,'fetch_nbu',offline)
        assert c.get('/api/fx/usd-uah').status_code==503
