"""v9 stage-two access scoping and local/offline recovery rehearsal.

No production resources. The archive is created with synthetic records only.
"""
import json
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func

import app.main as main
from app.backup_v9 import (UnsafeBackup, add_integrity, verify_snapshot,
                           rehearsal_restore_new_sqlite, MAX_ARCHIVE_SIZE)

ADMIN = {'Authorization':'Bearer '+'t'*40}
AUDIT = {'Authorization':'Bearer '+'a'*40}


def test_scoped_readiness_token_has_no_other_admin_access(monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN','t'*40)
    monkeypatch.setenv('BEERSHOP_V9_AUDIT_TOKEN','a'*40)
    with TestClient(main.app) as c:
        allowed=c.get('/api/admin/v9/readiness',headers=AUDIT)
        assert allowed.status_code==200,allowed.text
        assert allowed.json()['mode']=='READ_ONLY_AUDIT'
        for uri in ['/api/admin/orders','/api/admin/products','/api/admin/inventory/summary',
                    '/api/admin/maintenance/export','/api/admin/pricing/policy',
                    '/api/admin/ops/orders','/api/admin/crm/orders']:
            assert c.get(uri,headers=AUDIT).status_code==401,uri
        assert c.post('/api/admin/products',headers=AUDIT,json={}).status_code==401
        assert c.put('/api/admin/pricing/policy',headers=AUDIT,json={}).status_code==401
        assert c.get('/api/admin/v9/readiness',headers=ADMIN).status_code==200
        assert c.get('/api/admin/v9/readiness',headers={'Authorization':'Bearer '+'x'*40}).status_code==401
        for value in ['a'*31,'']:
            monkeypatch.setenv('BEERSHOP_V9_AUDIT_TOKEN',value)
            assert c.get('/api/admin/v9/readiness',headers=AUDIT).status_code==401
    monkeypatch.delenv('BEERSHOP_V9_AUDIT_TOKEN',raising=False)


def _synthetic_export():
    engine=create_engine('sqlite:///:memory:')
    main.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(main.Store.__table__.insert(),{
            'id':1,'slug':'beershop','name':'Synthetic fixture'})
        conn.execute(main.Category.__table__.insert(),{
            'id':1,'store_id':1,'slug':'test','name':'Test'})
        conn.execute(main.Product.__table__.insert(),{
            'id':1,'store_id':1,'category_id':1,'slug':'test-sku','name':'Synthetic Product',
            'description':'Test item','image_url':'','active':True})
        conn.execute(main.Variant.__table__.insert(),{
            'id':1,'store_id':1,'product_id':1,'sku':'TEST-001',
            'attributes':'{}','price':'50.00','currency':'USD','stock':2,'active':True})
        conn.execute(main.Order.__table__.insert(),{
            'id':1,'store_id':1,'request_key':'synthetic-unique-001',
            'name':'Synthetic Buyer','email':'buyer@example.test','phone':'0505559999',
            'delivery_address':'Testing only','status':'pending','payment_status':'unpaid',
            'payment_method':'iban','total':'501.00'})
        conn.execute(main.OrderItem.__table__.insert(),{
            'id':1,'order_id':1,'variant_id':1,'sku':'TEST-001',
            'name':'Synthetic Product','quantity':1,'unit_price':'501.00'})
        tables={t.name:[dict(r._mapping) for r in conn.execute(select(t)).all()]
                for t in main.Base.metadata.sorted_tables}
    engine.dispose()
    return add_integrity({
        'format':'beershop-snapshot-v1','created_at':'2026-11-10T10:00:00+00:00',
        'tables':tables,
    },main.Base.metadata)


def test_private_snapshot_export_contains_integrity_and_is_still_protected(monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN','t'*40)
    with TestClient(main.app) as c:
        assert c.get('/api/admin/maintenance/export').status_code==401
        result=c.get('/api/admin/maintenance/export',headers=ADMIN)
        assert result.status_code==200
        assert result.headers['cache-control']=='private, no-store'
        archive=result.json()
        assert archive['format']=='beershop-snapshot-v1'
        assert archive['integrity']['algorithm']=='sha256'
        assert verify_snapshot(archive,main.Base.metadata)['ok'] is True
        assert result.headers['x-robots-tag']=='noindex, nofollow'


def test_rehearsal_restore_counts_and_foreign_keys(tmp_path):
    payload=_synthetic_export()
    check=verify_snapshot(payload,main.Base.metadata)
    assert check['total_rows']>=6
    assert check['counts']['orders']==1
    target=tmp_path/'new-rehearsal.db'
    result=rehearsal_restore_new_sqlite(json.dumps(payload).encode('utf-8'),
                                       main.Base.metadata,target)
    assert result['mode']=='LOCAL_REHEARSAL_ONLY'
    assert target.exists()
    local=create_engine('sqlite:///'+str(target))
    with local.connect() as db:
        assert db.scalar(select(func.count()).select_from(main.Product))==1
        assert db.scalar(select(func.count()).select_from(main.Order))==1
        assert db.scalar(select(main.Order.total))==Decimal('501.00')
        assert db.scalar(select(main.Order.name))=='Synthetic Buyer'
    local.dispose()
    with pytest.raises(UnsafeBackup,match='NEW'):
        rehearsal_restore_new_sqlite(payload,main.Base.metadata,target)


def test_backup_detects_tampering_and_rejects_unsafe_targets(tmp_path):
    original=_synthetic_export()
    for key in ['integrity','tables']:
        broken=deepcopy(original)
        broken.pop(key)
        with pytest.raises(UnsafeBackup):
            verify_snapshot(broken,main.Base.metadata)
    broken=deepcopy(original)
    broken['tables']['variants'][0]['stock']=123456
    with pytest.raises(UnsafeBackup,match='SHA256'):
        verify_snapshot(broken,main.Base.metadata)
    broken=deepcopy(original)
    broken['integrity']['counts']['orders']=5
    with pytest.raises(UnsafeBackup,match='counts'):
        verify_snapshot(broken,main.Base.metadata)
    broken=deepcopy(original)
    broken['integrity']['schema_sha256']='0'*64
    with pytest.raises(UnsafeBackup,match='schema'):
        verify_snapshot(broken,main.Base.metadata)
    with pytest.raises(UnsafeBackup):
        verify_snapshot(b'X'*(MAX_ARCHIVE_SIZE+1),main.Base.metadata)
    legacy=deepcopy(original)
    legacy.pop('integrity')
    with pytest.raises(UnsafeBackup,match='legacy'):
        verify_snapshot(legacy,main.Base.metadata)

    new_file=tmp_path/'oops.txt'
    with pytest.raises(UnsafeBackup,match='NEW'):
        rehearsal_restore_new_sqlite(original,main.Base.metadata,new_file)
    assert not new_file.exists()


def test_rehearsal_refuses_orphan_foreign_keys_and_cleans_up(tmp_path):
    broken=_synthetic_export()
    broken['tables']['order_items'][0]['variant_id']=987654
    broken=add_integrity(broken,main.Base.metadata)
    target=tmp_path/'no-orphans.db'
    with pytest.raises(Exception):
        rehearsal_restore_new_sqlite(broken,main.Base.metadata,target)
    assert not target.exists()
