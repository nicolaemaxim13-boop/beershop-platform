"""BeerShop 4.0 operational foundation.

No bank, KeyCRM or Nova Poshta network calls are made by this module.
Manual confirmations are explicit and audited, not presented as API verification.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, ForeignKey, Integer, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, Session


def setup_ops(app, Base, Order, OrderItem, auth, session):
    class OrderOperations(Base):
        __tablename__ = 'order_operations'
        order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), primary_key=True)
        bank_transaction_id: Mapped[str | None] = mapped_column(String(120), unique=True)
        bank_reference: Mapped[str | None] = mapped_column(String(300))
        bank_confirmed_at: Mapped[str | None] = mapped_column(String(40))
        crm_order_id: Mapped[str | None] = mapped_column(String(80), unique=True)
        crm_synced_at: Mapped[str | None] = mapped_column(String(40))
        tracking_number: Mapped[str | None] = mapped_column(String(20), unique=True)
        delivery_status: Mapped[str] = mapped_column(String(30), default='not_created')
        delivery_updated_at: Mapped[str | None] = mapped_column(String(40))

    class OperationsAudit(Base):
        __tablename__ = 'operations_audit'
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), index=True)
        event: Mapped[str] = mapped_column(String(70))
        at: Mapped[str] = mapped_column(String(40))
        note: Mapped[str] = mapped_column(String(250), default='')

    class ConfirmBankPayment(BaseModel):
        transaction_id: str = Field(min_length=6, max_length=120)
        amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
        currency: Literal['UAH']
        reference: str = Field(min_length=6, max_length=300)
        attestation: Literal['BANK_STATEMENT_CHECKED']

    class WaybillIn(BaseModel):
        tracking_number: str = Field(pattern=r'^[0-9]{14}$')
        attestation: Literal['WAYBILL_CHECKED_IN_NOVA_POSHTA']

    class DeliveryStatusIn(BaseModel):
        status: Literal['handed_over', 'delivered']
        attestation: Literal['STATUS_CHECKED_IN_NOVA_POSHTA']

    class CrmConfirm(BaseModel):
        external_order_id: str = Field(min_length=1, max_length=80)
        attestation: Literal['ORDER_CONFIRMED_IN_KEYCRM']

    def utc():
        return datetime.now(timezone.utc).isoformat()

    def get_order(db, order_id, lock=False):
        stmt = select(Order).where(Order.store_id == 1, Order.id == order_id)
        if lock:
            stmt = stmt.with_for_update()
        order = db.scalar(stmt)
        if not order:
            raise HTTPException(404, 'Замовлення не знайдено')
        return order

    def get_operation(db, order_id, create=False):
        op = db.get(OrderOperations, order_id)
        if op is None and create:
            op = OrderOperations(order_id=order_id)
            db.add(op)
            db.flush()
        return op

    def audit(db, order_id, event, note=''):
        db.add(OperationsAudit(order_id=order_id, event=event, at=utc(), note=note))

    def public_op(order, op):
        return {'id': order.id, 'invoice': f'BS-{order.id:06}',
                'buyer': order.name, 'total': str(order.total),
                'order_status': order.status, 'payment_status': order.payment_status,
                'bank_confirmed': bool(op and op.bank_confirmed_at),
                'crm_synced': bool(op and op.crm_synced_at),
                'crm_order_id': op.crm_order_id if op else None,
                'tracking_number': op.tracking_number if op else None,
                'delivery_status': op.delivery_status if op else 'not_created'}

    @app.get('/api/admin/ops/orders', dependencies=[Depends(auth)])
    def ops_orders(limit: int = Query(50, ge=1, le=100),
                   db: Session = Depends(session)):
        orders = db.scalars(select(Order).where(Order.store_id == 1).
                            order_by(Order.id.desc()).limit(limit)).all()
        if not orders:
            return []
        by_id = {x.order_id: x for x in db.scalars(select(OrderOperations).
                 where(OrderOperations.order_id.in_([o.id for o in orders]))).all()}
        return [public_op(o, by_id.get(o.id)) for o in orders]

    @app.get('/api/admin/ops/summary', dependencies=[Depends(auth)])
    def ops_summary(db: Session = Depends(session)):
        orders = db.scalars(select(Order).where(Order.store_id == 1)).all()
        by_id = {x.order_id: x for x in db.scalars(select(OrderOperations)).all()}
        return {
            'orders': len(orders),
            'awaiting_payment': sum(o.payment_status != 'paid' and o.status != 'cancelled' for o in orders),
            'payments_confirmed': sum(o.payment_status == 'paid' for o in orders),
            'pending_keycrm': sum(not by_id.get(o.id) or not by_id[o.id].crm_synced_at for o in orders),
            'pending_shipment': sum(o.payment_status == 'paid' and o.status != 'cancelled' and (
                not by_id.get(o.id) or not by_id[o.id].tracking_number) for o in orders)
        }

    @app.post('/api/admin/ops/orders/{order_id}/bank-confirm', dependencies=[Depends(auth)])
    def bank_confirm(order_id: int, body: ConfirmBankPayment, db: Session = Depends(session)):
        try:
            order = get_order(db, order_id, lock=True)
            if order.status == 'cancelled':
                raise HTTPException(409, 'Скасоване замовлення не можна оплачувати')
            op = get_operation(db, order_id, create=True)
            ref = f'BS-{order.id:06}'
            if ref not in body.reference.upper():
                raise HTTPException(409, 'Призначення платежу не містить ' + ref)
            if body.amount != order.total:
                raise HTTPException(409, 'Сума платежу не збігається з рахунком')
            tx = body.transaction_id.strip()
            if len(tx) < 6:
                raise HTTPException(422, 'Недійсний ID транзакції')
            other = db.scalar(select(OrderOperations).where(OrderOperations.bank_transaction_id == tx))
            if other and other.order_id != order_id:
                raise HTTPException(409, 'Транзакція вже прив’язана до іншого замовлення')
            if order.payment_status == 'paid' or op.bank_confirmed_at:
                if op.bank_transaction_id == tx and order.payment_status == 'paid':
                    return public_op(order, op)
                raise HTTPException(409, 'Замовлення вже має підтверджену оплату')
            op.bank_transaction_id = tx
            op.bank_reference = body.reference.strip()
            op.bank_confirmed_at = utc()
            order.payment_status = 'paid'
            audit(db, order.id, 'bank_payment_manually_confirmed', 'UAH · bank statement checked by operator')
            db.commit()
            return public_op(order, op)
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Ідентифікатор транзакції вже використано')
        except Exception:
            db.rollback()
            raise

    @app.post('/api/admin/ops/orders/{order_id}/nova-poshta/waybill', dependencies=[Depends(auth)])
    def register_waybill(order_id: int, body: WaybillIn, db: Session = Depends(session)):
        try:
            order = get_order(db, order_id, lock=True)
            if order.status == 'cancelled' or order.payment_status != 'paid':
                raise HTTPException(409, 'Для ТТН потрібна підтверджена оплата й активне замовлення')
            op = get_operation(db, order_id, create=True)
            if op.tracking_number:
                if op.tracking_number == body.tracking_number:
                    return public_op(order, op)
                raise HTTPException(409, 'ТТН уже зареєстровано; потрібна окрема процедура коригування')
            op.tracking_number = body.tracking_number
            op.delivery_status = 'created'
            op.delivery_updated_at = utc()
            audit(db, order.id, 'nova_poshta_waybill_manually_registered', 'External waybill checked')
            db.commit()
            return public_op(order, op)
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Цей номер ТТН уже використовується')
        except Exception:
            db.rollback()
            raise

    @app.patch('/api/admin/ops/orders/{order_id}/nova-poshta/status', dependencies=[Depends(auth)])
    def delivery_status(order_id: int, body: DeliveryStatusIn, db: Session = Depends(session)):
        try:
            order = get_order(db, order_id, lock=True)
            op = get_operation(db, order_id)
            if not op or not op.tracking_number:
                raise HTTPException(409, 'Спочатку зареєструйте справжню ТТН')
            allowed = {'created': 'handed_over', 'handed_over': 'delivered'}
            if body.status == op.delivery_status:
                return public_op(order, op)
            if allowed.get(op.delivery_status) != body.status:
                raise HTTPException(409, 'Неприпустимий перехід статусу доставки')
            op.delivery_status = body.status
            op.delivery_updated_at = utc()
            audit(db, order.id, 'nova_poshta_status_manually_confirmed', body.status)
            db.commit()
            return public_op(order, op)
        except Exception:
            db.rollback()
            raise

    @app.get('/api/admin/ops/orders/{order_id}/keycrm-payload', dependencies=[Depends(auth)])
    def keycrm_payload(order_id: int, source_id: int = Query(..., gt=0),
                       db: Session = Depends(session)):
        order = get_order(db, order_id)
        items = db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all()
        return {
            'source_id': source_id, 'source_uuid': f'BS-{order.id:06}',
            'buyer': {'full_name': order.name, 'email': order.email, 'phone': order.phone},
            'shipping': {'shipping_secondary_line': order.delivery_address},
            'products': [{'sku': line.sku, 'name': line.name,
                          'quantity': line.quantity, 'price': str(line.unit_price)}
                         for line in items]
        }

    @app.post('/api/admin/ops/orders/{order_id}/keycrm-confirm', dependencies=[Depends(auth)])
    def keycrm_confirm(order_id: int, body: CrmConfirm, db: Session = Depends(session)):
        try:
            order = get_order(db, order_id, lock=True)
            op = get_operation(db, order_id, create=True)
            external = body.external_order_id.strip()
            if not external:
                raise HTTPException(422, 'Потрібен ID замовлення в KeyCRM')
            other = db.scalar(select(OrderOperations).where(OrderOperations.crm_order_id == external))
            if other and other.order_id != order_id:
                raise HTTPException(409, 'Цей ID KeyCRM вже використаний')
            if op.crm_order_id:
                if op.crm_order_id == external:
                    return public_op(order, op)
                raise HTTPException(409, 'Замовлення вже синхронізоване з іншим ID KeyCRM')
            op.crm_order_id = external
            op.crm_synced_at = utc()
            audit(db, order.id, 'keycrm_export_manually_confirmed', 'External CRM order ID recorded')
            db.commit()
            return public_op(order, op)
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Цей ID KeyCRM уже використовується')
        except Exception:
            db.rollback()
            raise

    @app.get('/api/admin/ops/orders/{order_id}/audit', dependencies=[Depends(auth)])
    def ops_audit(order_id: int, db: Session = Depends(session)):
        get_order(db, order_id)
        entries = db.scalars(select(OperationsAudit).where(OperationsAudit.order_id == order_id).
                             order_by(OperationsAudit.id.desc()).limit(100)).all()
        return [{'at': a.at, 'event': a.event, 'note': a.note} for a in entries]
