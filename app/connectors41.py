"""BeerShop 4.1 opt-in provider connectors.

External calls occur ONLY on explicit authenticated admin actions.
No real bank writes, shipment creation, webhooks, cron, or automatic payment confirmation.
Keys are environment secrets; fixed HTTPS endpoints and no following redirects.
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import String, ForeignKey, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, Session

KEYCRM_ORIGIN = 'https://openapi.keycrm.app/v1'
NP_ENDPOINT = 'https://api.novaposhta.ua/v2.0/json/'
PRIVATBANK_SETTINGS = 'https://acp.privatbank.ua/api/statements/settings'
_GATE = threading.Lock()
_LAST_CALL = [0.0]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_json(url: str, method: str, payload=None, headers=None, *, keycrm=False):
    """Single no-redirect request. Generic errors never disclose credentials or buyer data."""
    if keycrm:
        with _GATE:
            if time.monotonic() - _LAST_CALL[0] < 3.1:
                raise HTTPException(429, 'KeyCRM API: обмеження запитів, повторіть трохи пізніше')
            _LAST_CALL[0] = time.monotonic()
    encoded = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = Request(url, data=encoded, method=method, headers={
        'Accept': 'application/json', 'User-Agent': 'BeerShop-4.1',
        **({'Content-Type': 'application/json'} if encoded is not None else {}),
        **(headers or {})
    })
    try:
        with build_opener(_NoRedirect()).open(request, timeout=7) as response:
            if response.status not in (200, 201):
                raise HTTPException(502, 'Зовнішній сервіс повернув неочікувану відповідь')
            raw = response.read(131073)
            if len(raw) > 131072:
                raise HTTPException(502, 'Зовнішній сервіс повернув завеликі дані')
            data = json.loads(raw)
            if not isinstance(data, (dict, list)):
                raise ValueError('Non-object provider JSON')
            return data
    except HTTPError as exc:
        if exc.code == 429:
            raise HTTPException(503, 'Постачальник тимчасово обмежив кількість запитів') from None
        if exc.code in (401, 403):
            raise HTTPException(503, 'Перевірте чинність та права API-ключа постачальника') from None
        # POST may have been accepted despite a provider/network exception.
        raise HTTPException(502, 'Постачальник повернув помилку. Стан операції потрібно перевірити вручну') from None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise HTTPException(502, 'Зовнішній сервіс недоступний або повернув некоректні дані') from None


def _keycrm_key():
    token = os.getenv('KEYCRM_API_KEY', '').strip()
    if not token:
        raise HTTPException(503, 'Ключ KeyCRM ще не налаштовано в Render')
    return token


def keycrm_sources():
    data = _request_json(KEYCRM_ORIGIN+'/order/source?limit=100', 'GET',
                         headers={'Authorization':'Bearer '+_keycrm_key()}, keycrm=True)
    raw = data.get('data', []) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise HTTPException(502, 'Невірний формат джерел KeyCRM')
    return [{'id': e.get('id'), 'name': str(e.get('name') or '')[:120]}
            for e in raw[:100] if isinstance(e, dict) and isinstance(e.get('id'), int)]


def send_keycrm_order(payload):
    return _request_json(KEYCRM_ORIGIN+'/order', 'POST', payload=payload,
                         headers={'Authorization':'Bearer '+_keycrm_key()}, keycrm=True)


def read_keycrm_order(external_id):
    if not re.fullmatch(r'[1-9][0-9]{0,17}', str(external_id)):
        raise HTTPException(422, 'Потрібен числовий ID замовлення KeyCRM')
    return _request_json(KEYCRM_ORIGIN+'/order/'+str(external_id)+'?include=status', 'GET',
                         headers={'Authorization':'Bearer '+_keycrm_key()}, keycrm=True)


def privatbank_availability():
    token = os.getenv('PRIVATBANK_STATEMENT_TOKEN', '').strip()
    if not token or os.getenv('PRIVATBANK_READ_ENABLED', '').strip() != '1':
        raise HTTPException(503, 'Потрібні токен Автоклієнта та дозвіл PRIVATBANK_READ_ENABLED=1')
    data = _request_json(PRIVATBANK_SETTINGS, 'GET', headers={
        'token': token, 'Content-Type': 'application/json;charset=utf8'})
    if (not isinstance(data, dict) or data.get('status') != 'SUCCESS'
            or data.get('type') != 'settings' or not isinstance(data.get('settings'), dict)):
        raise HTTPException(502, 'ПриватБанк не підтвердив доступ до налаштувань API')
    settings = data['settings']
    return {'settings_access_verified': True,
            'statements_access_verified': False,
            'statements_available_now': settings.get('phase') == 'WRK'
                and settings.get('work_balance') == 'N',
            'payment_automation': False}


def track_nova_poshta(number):
    if not re.fullmatch(r'[0-9]{14}', str(number)):
        raise HTTPException(422, 'Невірний формат ТТН')
    key = os.getenv('NOVA_POSHTA_API_KEY', '').strip()
    if not key:
        raise HTTPException(503, 'Ключ Нової пошти ще не налаштовано в Render')
    data = _request_json(NP_ENDPOINT, 'POST', payload={
        'apiKey': key, 'modelName': 'TrackingDocument', 'calledMethod': 'getStatusDocuments',
        'methodProperties': {'Documents':[{'DocumentNumber':number}]}
    })
    if not isinstance(data, dict) or data.get('success') is not True:
        raise HTTPException(502, 'Нова пошта не підтвердила відповідь. Перевірте ключ і ТТН')
    rows = data.get('data')
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise HTTPException(502, 'Нова пошта не повернула інформації про ТТН')
    first = rows[0]
    if str(first.get('Number') or first.get('DocumentNumber') or '') != number:
        raise HTTPException(502, 'Відповідь Нової пошти не збігається із запитаною ТТН')
    return {
        'tracking_number': number,
        'status': str(first.get('Status') or '')[:250],
        'status_code': str(first.get('StatusCode') or '')[:25],
        'scheduled_delivery': str(first.get('ScheduledDeliveryDate') or '')[:30],
        'source': 'Nova Poshta',
        'checked_at': datetime.now(timezone.utc).isoformat()
    }


def nova_delivery_status(code):
    # Unknown, refused, redirected and return codes need operator review.
    if code == '1':
        return 'created'
    if code in {'4', '5', '6', '7', '8', '41', '101'}:
        return 'handed_over'
    if code in {'9', '10', '11'}:
        return 'delivered'
    return None


def build_keycrm_payload(order, lines, source_id):
    return {
        'source_id': source_id,
        'source_uuid': f'BS-{order.id:06}',
        'buyer': {'full_name':order.name, 'email':order.email, 'phone':order.phone},
        'shipping': {'shipping_secondary_line':order.delivery_address},
        'products': [{'sku':x.sku, 'name':x.name, 'quantity':x.quantity,
                      'price':str(x.unit_price)} for x in lines]
    }


def setup_connectors41(app, Base, Order, OrderItem, Ops, Audit, auth, session):
    class CrmDispatch(Base):
        __tablename__ = 'crm_dispatches'
        order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), primary_key=True)
        state: Mapped[str] = mapped_column(String(30), default='reserved')
        source_id: Mapped[int] = mapped_column()
        created_at: Mapped[str] = mapped_column(String(40))
        external_id: Mapped[str | None] = mapped_column(String(80))

    class SyncRequest(BaseModel):
        source_id: int = Field(gt=0)
        attestation: Literal['SEND_PERSONAL_DATA_TO_KEYCRM']

    class CrmRefresh(BaseModel):
        source_id: int = Field(gt=0, le=2147483647)
        external_order_id: str = Field(pattern=r'^[1-9][0-9]{0,17}$')
        attestation: Literal['CHECK_ORDER_IN_KEYCRM']

    class TrackingSync(BaseModel):
        attestation: Literal['SYNC_EXISTING_WAYBILL_STATUS']

    def check_order(db, order_id, lock=False):
        stmt = select(Order).where(Order.store_id==1, Order.id==order_id)
        if lock:
            stmt = stmt.with_for_update()
        order = db.scalar(stmt)
        if not order:
            raise HTTPException(404, 'Замовлення не знайдено')
        return order

    def utc(): return datetime.now(timezone.utc).isoformat()

    @app.get('/api/admin/connectors41/status', dependencies=[Depends(auth)])
    def connector_status():
        return {
            'keycrm': {'configured':bool(os.getenv('KEYCRM_API_KEY','').strip()),
                       'outbound_enabled': os.getenv('KEYCRM_SYNC_ENABLED','').strip()=='1'},
            'nova_poshta': {'configured':bool(os.getenv('NOVA_POSHTA_API_KEY','').strip()),
                            'mode':'read_only_tracking'},
            'privatbank': {'configured':bool(os.getenv('PRIVATBANK_STATEMENT_TOKEN','').strip()),
                           'read_enabled':os.getenv('PRIVATBANK_READ_ENABLED','').strip()=='1',
                           'mode':'availability_check_only',
                           'statements_access_verified':False},
            'payment_automation': False
        }

    @app.get('/api/admin/connectors41/keycrm/sources', dependencies=[Depends(auth)])
    def sources():
        return {'sources': keycrm_sources()}

    @app.get('/api/admin/connectors41/privatbank/availability', dependencies=[Depends(auth)])
    def bank_availability():
        return privatbank_availability()

    @app.get('/api/admin/connectors41/snapshot/{order_id}', dependencies=[Depends(auth)])
    def snapshot(order_id: int, db: Session=Depends(session)):
        check_order(db, order_id)
        result = {'order_id':order_id}
        for provider, event in [('keycrm','keycrm_status_verified'),
                                ('nova_poshta','nova_poshta_status_verified')]:
            row = db.scalar(select(Audit).where(Audit.order_id==order_id, Audit.event==event).
                            order_by(Audit.id.desc()).limit(1))
            result[provider] = {'checked_at':row.at, **json.loads(row.note)} if row else None
        return result

    @app.post('/api/admin/connectors41/keycrm/refresh/{order_id}', dependencies=[Depends(auth)])
    def crm_refresh(order_id: int, body: CrmRefresh, db: Session=Depends(session)):
        order = check_order(db, order_id, lock=True)
        op = db.get(Ops, order_id)
        dispatch = db.get(CrmDispatch, order_id)
        if op and op.crm_order_id and op.crm_order_id != body.external_order_id:
            raise HTTPException(409, 'Замовлення вже пов’язане з іншим ID KeyCRM')
        if dispatch and dispatch.source_id != body.source_id:
            raise HTTPException(409, 'Джерело не збігається з історією відправлення')
        remote = read_keycrm_order(body.external_order_id)
        if (not isinstance(remote, dict) or type(remote.get('id')) is not int
                or str(remote['id']) != body.external_order_id
                or type(remote.get('source_id')) is not int
                or remote['source_id'] != body.source_id
                or remote.get('source_uuid') != f'BS-{order.id:06}'):
            raise HTTPException(409, 'ID, джерело або номер замовлення KeyCRM не збігаються')
        status = remote.get('status')
        status_id = remote.get('status_id')
        if (type(status_id) is not int or not 0 < status_id <= 2147483647 or not isinstance(status, dict)
                or status.get('id') != status_id or not isinstance(status.get('name'), str)):
            raise HTTPException(502, 'KeyCRM повернув некоректний статус')
        if op is None:
            op = Ops(order_id=order_id)
            db.add(op)
        op.crm_order_id = body.external_order_id
        op.crm_synced_at = utc()
        if dispatch:
            dispatch.state = 'reconciled'
            dispatch.external_id = body.external_order_id
        state = {'external_id':body.external_order_id, 'source_id':body.source_id,
                 'status_id':status_id}
        db.add(Audit(order_id=order_id, event='keycrm_status_verified', at=utc(),
                     note=json.dumps(state, ensure_ascii=False, separators=(',',':'))))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'ID KeyCRM уже прив’язаний до іншого замовлення') from None
        return {'order_id':order_id, **state, 'payment_automation':False}

    @app.get('/api/admin/connectors41/keycrm/dispatch/{order_id}', dependencies=[Depends(auth)])
    def dispatch_status(order_id: int, db: Session=Depends(session)):
        check_order(db, order_id)
        row = db.get(CrmDispatch, order_id)
        return {'order_id':order_id, 'attempted':row is not None,
                'state':row.state if row else 'never_sent',
                'source_id':row.source_id if row else None,
                'external_id':row.external_id if row else None}

    @app.post('/api/admin/connectors41/keycrm/sync/{order_id}', dependencies=[Depends(auth)])
    def keycrm_sync(order_id: int, body: SyncRequest, db: Session=Depends(session)):
        if os.getenv('KEYCRM_SYNC_ENABLED', '').strip() != '1':
            raise HTTPException(503, 'Відправлення в KeyCRM вимкнено. Налаштуйте KEYCRM_SYNC_ENABLED=1')
        _keycrm_key()
        order = check_order(db, order_id, lock=True)
        if order.status == 'cancelled':
            raise HTTPException(409, 'Скасоване замовлення не можна передавати в CRM')
        op = db.get(Ops, order_id)
        if op and op.crm_order_id:
            return {'state':'already_linked', 'order_id':order_id,
                    'external_id':op.crm_order_id}
        if db.get(CrmDispatch, order_id):
            raise HTTPException(409, 'Відправлення вже виконувалося. Звірте source_uuid у KeyCRM вручну; повторний POST заблоковано.')
        lines = db.scalars(select(OrderItem).where(OrderItem.order_id==order_id)).all()
        if not lines:
            raise HTTPException(409, 'Замовлення не містить товарів')
        payload = build_keycrm_payload(order, lines, body.source_id)
        # Commit a durable reservation BEFORE any external write; no automatic retry.
        db.add(CrmDispatch(order_id=order_id, source_id=body.source_id,
                           state='needs_reconciliation', created_at=utc()))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Відправлення вже зарезервовано іншим запитом')
        # HTTP errors/timeouts have ambiguous remote outcome. Reservation remains.
        response = send_keycrm_order(payload)
        remote = response.get('id') if isinstance(response,dict) else None
        if remote is None and isinstance(response,dict):
            inner=response.get('data')
            if isinstance(inner,dict): remote=inner.get('id')
        if type(remote) is not int or remote <= 0:
            raise HTTPException(502, 'KeyCRM не повернув ID. Перевірте source_uuid у CRM вручну; повторний POST заблоковано.')
        remote_id = str(remote).strip()
        if not remote_id or len(remote_id)>80:
            raise HTTPException(502, 'KeyCRM повернув некоректний ID. Перевірте замовлення вручну.')
        op = db.get(Ops, order_id)
        if op is None:
            op = Ops(order_id=order_id)
            db.add(op)
        if op.crm_order_id and op.crm_order_id != remote_id:
            raise HTTPException(409, 'CRM ID відрізняється від уже підтвердженого ID')
        op.crm_order_id=remote_id
        op.crm_synced_at=utc()
        row=db.get(CrmDispatch,order_id)
        row.state='confirmed'
        row.external_id=remote_id
        db.add(Audit(order_id=order_id,event='keycrm_sent_via_api',at=utc(),
                     note='source_uuid matched order ID; provider returned external ID'))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Відправлення виконано, але ID конфліктує. Перевірте KeyCRM вручну.')
        return {'state':'confirmed', 'order_id':order_id, 'external_id':remote_id,
                'source_uuid':payload['source_uuid']}

    @app.get('/api/admin/connectors41/nova-poshta/tracking/{order_id}', dependencies=[Depends(auth)])
    def tracking(order_id: int, db: Session=Depends(session)):
        check_order(db, order_id)
        op=db.get(Ops,order_id)
        if not op or not op.tracking_number:
            raise HTTPException(409, 'Спочатку внесіть справжню ТТН у вкладці «Операції 4.0»')
        return track_nova_poshta(op.tracking_number)

    @app.post('/api/admin/connectors41/nova-poshta/sync/{order_id}', dependencies=[Depends(auth)])
    def tracking_sync(order_id: int, body: TrackingSync, db: Session=Depends(session)):
        order = check_order(db, order_id, lock=True)
        if order.status == 'cancelled':
            raise HTTPException(409, 'Скасоване замовлення потребує ручної перевірки доставки')
        op = db.get(Ops, order_id)
        if not op or not op.tracking_number:
            raise HTTPException(409, 'Спочатку зареєструйте справжню ТТН')
        data = track_nova_poshta(op.tracking_number)
        mapped = nova_delivery_status(data['status_code'])
        ranks = {'not_created':0, 'created':1, 'handed_over':2, 'delivered':3}
        previous = op.delivery_status
        applicable = mapped is not None and ranks.get(mapped, -1) >= ranks.get(previous, 99)
        if applicable:
            op.delivery_status = mapped
            op.delivery_updated_at = utc()
        state = {'status_code':data['status_code'], 'delivery_status':op.delivery_status,
                 'changed':previous != op.delivery_status, 'review_required':not applicable}
        db.add(Audit(order_id=order_id, event='nova_poshta_status_verified', at=utc(),
                     note=json.dumps(state, separators=(',',':'))))
        db.commit()
        return {'order_id':order_id, **state, 'payment_automation':False}

