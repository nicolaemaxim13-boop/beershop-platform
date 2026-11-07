"""First-party CRM. All reads and writes require the existing admin identity."""
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column


def utc():
    return datetime.now(timezone.utc).isoformat()


def identity_key(email, phone):
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10 and digits.startswith('0'):
        digits = '38' + digits
    # Require both contacts to match; a shared phone alone must not merge people.
    value = email.strip().casefold() + '\0' + digits
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class Input(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')


class CustomerIn(Input):
    name: str = Field(min_length=2, max_length=180)
    email: str = Field(min_length=5, max_length=254)
    phone: str = Field(min_length=5, max_length=50)


class CustomerEdit(CustomerIn):
    version: int = Field(ge=1)


class ManagerIn(Input):
    name: str = Field(min_length=2, max_length=100)
    active: bool = True


class AssignmentIn(Input):
    manager_id: int | None = Field(default=None, gt=0)
    priority: Literal['low', 'normal', 'high'] = 'normal'
    version: int = Field(ge=1)


class NoteIn(Input):
    body: str = Field(min_length=1, max_length=2000)
    request_key: str = Field(min_length=8, max_length=80)


def setup_crm(app, Base, Order, OrderItem, Ops, Audit, auth, session, order_dict, inventory_order, inventory_backfill):
    class Customer(Base):
        __tablename__ = 'crm_customers'
        id: Mapped[int] = mapped_column(primary_key=True)
        store_id: Mapped[int] = mapped_column(ForeignKey('stores.id'), index=True)
        identity_key: Mapped[str] = mapped_column(String(64))
        name: Mapped[str] = mapped_column(String(180))
        email: Mapped[str] = mapped_column(String(254))
        phone: Mapped[str] = mapped_column(String(50))
        version: Mapped[int] = mapped_column(Integer, default=1)
        created_at: Mapped[str] = mapped_column(String(40), default=utc)
        __table_args__ = (UniqueConstraint('store_id', 'identity_key'),)

    class Manager(Base):
        __tablename__ = 'crm_managers'
        id: Mapped[int] = mapped_column(primary_key=True)
        store_id: Mapped[int] = mapped_column(ForeignKey('stores.id'), index=True)
        name: Mapped[str] = mapped_column(String(100))
        active: Mapped[bool] = mapped_column(Boolean, default=True)
        __table_args__ = (UniqueConstraint('store_id', 'name'),)

    class Details(Base):
        __tablename__ = 'crm_order_details'
        order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), primary_key=True)
        customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), index=True)
        manager_id: Mapped[int | None] = mapped_column(ForeignKey('crm_managers.id'), index=True)
        priority: Mapped[str] = mapped_column(String(10), default='normal')
        version: Mapped[int] = mapped_column(Integer, default=1)
        created_at: Mapped[str | None] = mapped_column(String(40))

    class Note(Base):
        __tablename__ = 'crm_notes'
        id: Mapped[int] = mapped_column(primary_key=True)
        customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), index=True)
        order_id: Mapped[int | None] = mapped_column(ForeignKey('orders.id'), index=True)
        body: Mapped[str] = mapped_column(String(2000))
        kind: Mapped[str] = mapped_column(String(30), default='note')
        request_key: Mapped[str] = mapped_column(String(80), unique=True)
        created_at: Mapped[str] = mapped_column(String(40), default=utc)

    router = APIRouter(prefix='/api/admin/crm', dependencies=[Depends(auth)])

    def get_customer(db, customer_id):
        row = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.store_id == 1))
        if row is None:
            raise HTTPException(404, 'Клієнта не знайдено')
        return row

    def get_order(db, order_id, lock=False):
        stmt = select(Order).where(Order.store_id == 1, Order.id == order_id)
        if lock:
            stmt = stmt.with_for_update()
        row = db.scalar(stmt)
        if row is None:
            raise HTTPException(404, 'Замовлення не знайдено')
        return row

    def attach_order(db, order, historical=False):
        if db.get(Details, order.id):
            return
        key = identity_key(order.email, order.phone)
        customer = db.scalar(select(Customer).where(Customer.store_id == order.store_id, Customer.identity_key == key))
        if customer is None:
            try:
                with db.begin_nested():
                    customer = Customer(store_id=order.store_id, identity_key=key,
                                        name=order.name, email=order.email, phone=order.phone)
                    db.add(customer)
                    db.flush()
            except IntegrityError:
                customer = db.scalar(select(Customer).where(Customer.store_id == order.store_id, Customer.identity_key == key))
                if customer is None:
                    raise
        db.add(Details(order_id=order.id, customer_id=customer.id,
                       created_at=None if historical else utc()))
        db.flush()

    def backfill(db):
        # Parent row locks make startup backfill safe during overlapping Render deploys.
        missing = db.scalars(select(Order).where(Order.store_id == 1,
                             ~select(Details.order_id).where(Details.order_id == Order.id).exists()).
                             order_by(Order.id).with_for_update()).all()
        for order in missing:
            attach_order(db, order, historical=True)

    def customer_dict(row):
        return {key:getattr(row, key) for key in ('id','name','email','phone','version','created_at')}

    def manager_dict(row):
        return {'id':row.id, 'name':row.name, 'active':row.active}

    def note_dict(row):
        return {'id':row.id, 'body':row.body, 'kind':row.kind, 'order_id':row.order_id,
                'created_at':row.created_at}

    def list_row(order, details, manager, op):
        return {'id':order.id, 'invoice':f'BS-{order.id:06}', 'name':order.name,
                'email':order.email, 'phone':order.phone, 'total':str(order.total),
                'status':order.status, 'payment_status':order.payment_status,
                'customer_id':details.customer_id, 'priority':details.priority,
                'manager_id':details.manager_id, 'manager':manager.name if manager else None,
                'created_at':details.created_at, 'version':details.version,
                'delivery_status':op.delivery_status if op else 'not_created',
                'tracking_number':op.tracking_number if op else None}

    def order_rows():
        return (select(Order, Details, Manager, Ops).join(Details, Details.order_id == Order.id).
                outerjoin(Manager, Manager.id == Details.manager_id).outerjoin(Ops, Ops.order_id == Order.id))

    def search_pattern(q):
        return '%'+q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'

    @router.get('/summary')
    def summary(db: Session=Depends(session)):
        counts = dict(db.execute(select(Order.status, func.count()).where(Order.store_id == 1).group_by(Order.status)).all())
        waiting = db.scalar(select(func.count()).select_from(Order).where(
            Order.store_id == 1, Order.status != 'cancelled', Order.payment_status != 'paid'))
        return {'orders':sum(counts.values()), 'statuses':counts, 'awaiting_payment':waiting,
                'customers':db.scalar(select(func.count()).select_from(Customer).where(Customer.store_id == 1))}

    @router.post('/index-orders')
    def index_orders(db: Session=Depends(session)):
        backfill(db)
        inventory_backfill(db)
        db.commit()
        return {'status':'ok'}

    @router.get('/orders')
    def orders(q: str=Query('', max_length=100), status: Literal['all','pending','confirmed','picking','fulfilled','cancelled']='all',
               payment: Literal['all','paid','unpaid']='all', manager: str=Query('all', max_length=20),
               priority: Literal['all','low','normal','high']='all', page: int=Query(1, ge=1, le=1000000),
               limit: int=Query(25, ge=1, le=100), db: Session=Depends(session)):
        stmt = order_rows().where(Order.store_id == 1)
        if q.strip():
            pattern = search_pattern(q)
            conditions = [column.ilike(pattern, escape='\\') for column in (Order.name,Order.email,Order.phone)]
            match = re.fullmatch(r'(?:BS-)?0*([1-9][0-9]*)', q.strip(), re.I)
            if match and int(match[1]) <= 2147483647:
                conditions.append(Order.id == int(match[1]))
            stmt = stmt.where(or_(*conditions))
        if status != 'all': stmt = stmt.where(Order.status == status)
        if payment != 'all': stmt = stmt.where(Order.payment_status == payment)
        if priority != 'all': stmt = stmt.where(Details.priority == priority)
        if manager == 'unassigned': stmt = stmt.where(Details.manager_id.is_(None))
        elif manager != 'all':
            if not re.fullmatch(r'[1-9][0-9]{0,9}', manager):
                raise HTTPException(422, 'Невірний відповідальний')
            stmt = stmt.where(Details.manager_id == int(manager))
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.execute(stmt.order_by(Order.id.desc()).offset((page-1)*limit).limit(limit)).all()
        return {'items':[list_row(*row) for row in rows], 'total':total, 'page':page, 'limit':limit}

    @router.get('/orders/{order_id}')
    def order_detail(order_id: int, db: Session=Depends(session)):
        order = get_order(db, order_id)
        row = db.execute(order_rows().where(Order.id == order_id)).one()
        notes = db.scalars(select(Note).where(Note.order_id == order_id).order_by(Note.id.desc()).limit(100)).all()
        events = db.scalars(select(Audit).where(Audit.order_id == order_id).order_by(Audit.id.desc()).limit(100)).all()
        result = order_dict(db, order)
        products, variants = Base.metadata.tables['products'], Base.metadata.tables['variants']
        images = dict(db.execute(select(OrderItem.variant_id,products.c.image_url).
                      join(variants,variants.c.id == OrderItem.variant_id).
                      join(products,products.c.id == variants.c.product_id).
                      where(OrderItem.order_id == order_id,products.c.store_id == 1)).all())
        for item in result['items']:
            item['image_url'] = images.get(item['variant_id'],'')
        return {**result, **list_row(*row), 'customer':customer_dict(get_customer(db,row[1].customer_id)),
                'notes':[note_dict(n) for n in notes], 'inventory':inventory_order(db,order_id),
                'audit':[{'event':a.event, 'at':a.at, 'note':a.note} for a in events]}

    @router.patch('/orders/{order_id}')
    def assign(order_id: int, body: AssignmentIn, db: Session=Depends(session)):
        get_order(db, order_id, lock=True)
        if body.manager_id is not None:
            manager = db.scalar(select(Manager).where(Manager.id == body.manager_id, Manager.store_id == 1, Manager.active.is_(True)))
            if manager is None:
                raise HTTPException(422, 'Оберіть активного відповідального')
        result = db.execute(update(Details).where(Details.order_id == order_id, Details.version == body.version).
                            values(manager_id=body.manager_id, priority=body.priority, version=Details.version+1))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(409, 'Замовлення вже змінено. Оновіть картку перед збереженням.')
        db.add(Audit(order_id=order_id, event='crm_assignment_updated', at=utc(),
                     note=json.dumps({'manager_id':body.manager_id,'priority':body.priority})))
        db.commit()
        return order_detail(order_id, db)

    def append_note(db, customer_id, body, order_id=None):
        previous = db.scalar(select(Note).where(Note.request_key == body.request_key))
        if previous:
            if (previous.customer_id,previous.order_id,previous.body) != (customer_id,order_id,body.body):
                raise HTTPException(409, 'Ідентифікатор нотатки вже використано')
            return note_dict(previous)
        row = Note(customer_id=customer_id, order_id=order_id, body=body.body, request_key=body.request_key)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Нотатку вже збережено. Оновіть картку.') from None
        return note_dict(row)

    @router.post('/orders/{order_id}/notes', status_code=201)
    def order_note(order_id: int, body: NoteIn, db: Session=Depends(session)):
        get_order(db, order_id)
        return append_note(db, db.get(Details,order_id).customer_id, body, order_id)

    @router.get('/customers')
    def customers(q: str=Query('', max_length=100), page: int=Query(1, ge=1, le=1000000), limit: int=Query(25, ge=1, le=100),
                  db: Session=Depends(session)):
        stats = select(Details.customer_id, func.count().label('orders'),
                       func.sum(case(((Order.payment_status == 'paid') & (Order.status != 'cancelled'), Order.total), else_=0)).label('paid_total')).\
            join(Order, Order.id == Details.order_id).where(Order.store_id == 1).\
            group_by(Details.customer_id).subquery()
        stmt = select(Customer, stats.c.orders, stats.c.paid_total).outerjoin(stats,stats.c.customer_id == Customer.id).where(Customer.store_id == 1)
        if q.strip():
            pattern = search_pattern(q)
            stmt = stmt.where(or_(*[c.ilike(pattern, escape='\\') for c in (Customer.name,Customer.email,Customer.phone)]))
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.execute(stmt.order_by(Customer.id.desc()).offset((page-1)*limit).limit(limit)).all()
        return {'items':[{**customer_dict(c),'orders':count or 0,'paid_total':str(paid or '0.00')} for c,count,paid in rows],
                'total':total,'page':page,'limit':limit}

    @router.get('/customers/{customer_id}')
    def customer_detail(customer_id: int, db: Session=Depends(session)):
        customer = get_customer(db,customer_id)
        rows = db.execute(order_rows().where(Order.store_id == 1,Details.customer_id == customer_id).
                          order_by(Order.id.desc()).limit(100)).all()
        notes = db.scalars(select(Note).where(Note.customer_id == customer_id).order_by(Note.id.desc()).limit(100)).all()
        return {**customer_dict(customer),'orders':[list_row(*r) for r in rows], 'notes':[note_dict(n) for n in notes]}

    @router.post('/customers', status_code=201)
    def create_customer(body: CustomerIn, db: Session=Depends(session)):
        customer = Customer(store_id=1,identity_key=identity_key(body.email,body.phone),**body.model_dump())
        db.add(customer)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Клієнт із цими контактами вже існує') from None
        return customer_dict(customer)

    @router.patch('/customers/{customer_id}')
    def edit_customer(customer_id: int, body: CustomerEdit, db: Session=Depends(session)):
        get_customer(db,customer_id)
        try:
            result = db.execute(update(Customer).where(Customer.id == customer_id, Customer.version == body.version).
                                values(**body.model_dump(exclude={'version'}), identity_key=identity_key(body.email,body.phone),
                                       version=Customer.version+1))
            if result.rowcount != 1:
                raise HTTPException(409, 'Картку вже змінено. Оновіть її перед збереженням.')
            db.add(Note(customer_id=customer_id,body='Контактні дані оновлено',kind='contact_updated',
                        request_key='edit-'+str(customer_id)+'-'+str(body.version)))
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Ці контакти вже використовуються іншою карткою') from None
        return customer_detail(customer_id,db)

    @router.post('/customers/{customer_id}/notes', status_code=201)
    def customer_note(customer_id: int, body: NoteIn, db: Session=Depends(session)):
        get_customer(db,customer_id)
        return append_note(db,customer_id,body)

    @router.get('/managers')
    def managers(db: Session=Depends(session)):
        return [manager_dict(m) for m in db.scalars(select(Manager).where(Manager.store_id == 1).order_by(Manager.name)).all()]

    @router.post('/managers', status_code=201)
    def create_manager(body: ManagerIn, db: Session=Depends(session)):
        row = Manager(store_id=1,**body.model_dump())
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Таке ім’я вже є у списку відповідальних') from None
        return manager_dict(row)

    @router.patch('/managers/{manager_id}')
    def edit_manager(manager_id: int, body: ManagerIn, db: Session=Depends(session)):
        row = db.scalar(select(Manager).where(Manager.id == manager_id,Manager.store_id == 1))
        if row is None:
            raise HTTPException(404, 'Відповідального не знайдено')
        row.name, row.active = body.name, body.active
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Таке ім’я вже є у списку відповідальних') from None
        return manager_dict(row)

    app.include_router(router)
    return attach_order, backfill
