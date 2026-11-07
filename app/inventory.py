"""Available stock stays in variants.stock; physical stock includes open reservations."""
import hashlib
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, func, or_, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.exc import IntegrityError


def utc():
    return datetime.now(timezone.utc).isoformat()


class StockCount(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    on_hand: int = Field(ge=0, le=1000000)
    expected_available: int = Field(ge=0)
    expected_reserved: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=300)
    request_key: str = Field(min_length=8, max_length=80)


def setup_inventory(app, Base, Order, OrderItem, Variant, Product, Ops, Audit, auth, session):
    class Reservation(Base):
        __tablename__ = 'stock_reservations'
        item_id: Mapped[int] = mapped_column(ForeignKey('order_items.id'), primary_key=True)
        order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), index=True)
        variant_id: Mapped[int] = mapped_column(ForeignKey('variants.id'), index=True)
        quantity: Mapped[int] = mapped_column(Integer)
        state: Mapped[str] = mapped_column(String(20))
        created_at: Mapped[str] = mapped_column(String(40), default=utc)
        updated_at: Mapped[str] = mapped_column(String(40), default=utc)
        __table_args__ = (CheckConstraint('quantity > 0'),
                         CheckConstraint("state IN ('reserved','shipped','released')"))

    class Movement(Base):
        __tablename__ = 'stock_movements'
        id: Mapped[int] = mapped_column(primary_key=True)
        variant_id: Mapped[int] = mapped_column(ForeignKey('variants.id'), index=True)
        order_id: Mapped[int | None] = mapped_column(ForeignKey('orders.id'), index=True)
        event: Mapped[str] = mapped_column(String(30))
        available_delta: Mapped[int] = mapped_column(Integer)
        reserved_delta: Mapped[int] = mapped_column(Integer)
        reason: Mapped[str] = mapped_column(String(300), default='')
        request_key: Mapped[str | None] = mapped_column(String(80), unique=True)
        fingerprint: Mapped[str | None] = mapped_column(String(64))
        at: Mapped[str] = mapped_column(String(40), default=utc)

    def movement(db, variant_id, order_id, event, available, reserved, **extra):
        row = Movement(variant_id=variant_id, order_id=order_id, event=event,
                       available_delta=available, reserved_delta=reserved, **extra)
        db.add(row)
        return row

    def ensure_order(db, order, historical=True):
        # Callers hold the order lock. Old checkout already subtracted availability.
        items = db.scalars(select(OrderItem).where(OrderItem.order_id == order.id).
                           order_by(OrderItem.variant_id)).all()
        op = db.get(Ops, order.id)
        state = ('released' if order.status == 'cancelled' else 'shipped'
                 if order.status == 'fulfilled' or (op and op.delivery_status in {'handed_over','delivered'})
                 else 'reserved')
        if not historical:
            state = 'reserved'
        for item in items:
            existing = db.get(Reservation, item.id)
            if existing:
                if historical and existing.state == 'reserved' and state in {'shipped','released'}:
                    # A pre-4.3 writer may finish an order during an overlapping deploy.
                    # Its availability update is already committed; never replay it.
                    existing.state, existing.updated_at = state, utc()
                    movement(db,item.variant_id,order.id,'legacy_reconciled',0,-item.quantity,
                             reason='Previous order outcome reconciled; available stock unchanged')
                continue
            db.add(Reservation(item_id=item.id, order_id=order.id, variant_id=item.variant_id,
                               quantity=item.quantity, state=state))
            movement(db,item.variant_id,order.id,'legacy_indexed' if historical else 'reserved',
                     0 if historical else -item.quantity,0 if historical else item.quantity)
        db.flush()

    def locked_variants(db, order):
        ids = select(OrderItem.variant_id).where(OrderItem.order_id == order.id)
        return {v.id:v for v in db.scalars(select(Variant).where(Variant.store_id == order.store_id,
                    Variant.id.in_(ids)).order_by(Variant.id).with_for_update()).all()}

    def transition(db, order, target):
        variants = locked_variants(db,order)
        ensure_order(db,order)
        rows = db.scalars(select(Reservation).where(Reservation.order_id == order.id).
                          order_by(Reservation.variant_id)).all()
        for row in rows:
            if row.state == target:
                continue
            if row.state != 'reserved' or row.variant_id not in variants:
                raise HTTPException(409, 'Рух товару потребує окремої перевірки повернення')
            available = row.quantity if target == 'released' else 0
            if available:
                variants[row.variant_id].stock += available
            row.state, row.updated_at = target, utc()
            movement(db,row.variant_id,order.id,target,available,-row.quantity)
        db.flush()

    def reserve(db, order):
        ensure_order(db,order,historical=False)

    def release(db, order):
        transition(db,order,'released')

    def ship(db, order):
        if order.status == 'cancelled':
            raise HTTPException(409, 'Скасоване замовлення потребує перевірки повернення')
        transition(db,order,'shipped')

    def backfill(db):
        missing = select(OrderItem.order_id).where(
            ~select(Reservation.item_id).where(Reservation.item_id == OrderItem.id).exists())
        terminal = select(Order.id).outerjoin(Ops,Ops.order_id == Order.id).where(
            or_(Order.status.in_(['cancelled','fulfilled']),Ops.delivery_status.in_(['handed_over','delivered'])))
        stale = select(Reservation.order_id).where(Reservation.state == 'reserved',Reservation.order_id.in_(terminal))
        orders = db.scalars(select(Order).where(Order.store_id == 1,or_(Order.id.in_(missing),Order.id.in_(stale))).
                            order_by(Order.id).with_for_update()).all()
        for order in orders:
            locked_variants(db,order)
            ensure_order(db,order)

    def set_available(db, variant, value, reason, **extra):
        delta = value-variant.stock
        if delta or extra.get('request_key'):
            movement(db,variant.id,None,'stock_count',delta,0,reason=reason,**extra)
        variant.stock = value

    def order_state(db, order_id):
        rows = db.scalars(select(Reservation).where(Reservation.order_id == order_id)).all()
        return [{'variant_id':r.variant_id,'quantity':r.quantity,'state':r.state} for r in rows]

    def reserved_query():
        return select(Reservation.variant_id,func.sum(Reservation.quantity).label('reserved')).\
            where(Reservation.state == 'reserved').group_by(Reservation.variant_id).subquery()

    def catalog_query():
        reserved = reserved_query()
        return select(Product,Variant,func.coalesce(reserved.c.reserved,0).label('reserved')).\
            join(Variant,Variant.product_id == Product.id).\
            outerjoin(reserved,reserved.c.variant_id == Variant.id).\
            where(Product.store_id == 1,Variant.store_id == 1)

    def stock_dict(p,v,reserved):
        return {'variant_id':v.id,'product_id':p.id,'name':p.name,'sku':v.sku,
                'image_url':p.image_url,'active':p.active and v.active,
                'available':v.stock,'reserved':reserved,'on_hand':v.stock+reserved}

    router = APIRouter(prefix='/api/admin/inventory',dependencies=[Depends(auth)])

    @router.get('/summary')
    def summary(db: Session=Depends(session)):
        rows = catalog_query().subquery()
        # Aggregate only numeric balances; no customer records are loaded here.
        count,available,reserved = db.execute(select(func.count(),func.coalesce(func.sum(rows.c.stock),0),
                   func.coalesce(func.sum(rows.c.reserved),0)).select_from(rows)).one()
        return {'skus':count,'available':available,'reserved':reserved,'on_hand':available+reserved}

    @router.get('')
    def catalog(q: str=Query('',max_length=100), availability: Literal['all','reserved','available','out']='all',
                page: int=Query(1,ge=1,le=1000000), limit: int=Query(25,ge=1,le=100), db: Session=Depends(session)):
        stmt = catalog_query()
        if q.strip():
            pattern = '%'+q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            stmt = stmt.where(or_(Product.name.ilike(pattern,escape='\\'),Variant.sku.ilike(pattern,escape='\\')))
        if availability == 'available': stmt = stmt.where(Variant.stock > 0)
        if availability == 'out': stmt = stmt.where(Variant.stock == 0)
        if availability == 'reserved': stmt = stmt.where(stmt.selected_columns.reserved > 0)
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.execute(stmt.order_by(Variant.id).offset((page-1)*limit).limit(limit)).all()
        return {'items':[stock_dict(*r) for r in rows],'total':total,'page':page,'limit':limit}

    @router.get('/{variant_id}')
    def detail(variant_id: int, db: Session=Depends(session)):
        row = db.execute(catalog_query().where(Variant.id == variant_id)).one_or_none()
        if row is None:
            raise HTTPException(404, 'SKU не знайдено')
        reservations = db.scalars(select(Reservation).where(Reservation.variant_id == variant_id,
                  Reservation.state == 'reserved').order_by(Reservation.order_id.desc()).limit(100)).all()
        events = db.scalars(select(Movement).where(Movement.variant_id == variant_id).
                            order_by(Movement.id.desc()).limit(100)).all()
        return {**stock_dict(*row),
                'reservations':[{'order_id':r.order_id,'quantity':r.quantity} for r in reservations],
                'movements':[{key:getattr(m,key) for key in ('id','order_id','event','available_delta','reserved_delta','reason','at')} for m in events]}

    @router.patch('/{variant_id}')
    def count_stock(variant_id: int, body: StockCount, db: Session=Depends(session)):
        if db.bind.dialect.name == 'sqlite': db.execute(text('BEGIN IMMEDIATE'))
        variant = db.scalar(select(Variant).where(Variant.id == variant_id,Variant.store_id == 1).with_for_update())
        if variant is None:
            raise HTTPException(404, 'SKU не знайдено')
        fingerprint = hashlib.sha256((str(variant_id)+'\0'+body.model_dump_json()).encode()).hexdigest()
        previous = db.scalar(select(Movement).where(Movement.request_key == body.request_key))
        if previous:
            if previous.fingerprint != fingerprint:
                raise HTTPException(409, 'Ключ коригування вже використано')
            return detail(variant_id,db)
        reserved = db.scalar(select(func.coalesce(func.sum(Reservation.quantity),0)).where(
                             Reservation.variant_id == variant_id,Reservation.state == 'reserved'))
        if (variant.stock,reserved) != (body.expected_available,body.expected_reserved):
            raise HTTPException(409, 'Залишок або резерв змінився. Оновіть картку.')
        if body.on_hand < reserved:
            raise HTTPException(409, 'Фактичний залишок менший за резерв. Спершу перевірте замовлення.')
        set_available(db,variant,body.on_hand-reserved,body.reason,
                      request_key=body.request_key,fingerprint=fingerprint)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Ключ коригування вже використано. Оновіть картку.') from None
        return detail(variant_id,db)

    app.include_router(router)
    return reserve,release,ship,backfill,order_state,set_available
