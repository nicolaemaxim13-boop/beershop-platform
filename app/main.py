"""BeerShop v2: equipment catalog, SKUs, inventory-safe checkout, IBAN invoices.
Demo single-store scope; do not deploy without production hardening.
"""
import os
import json
import csv
import io
import secrets
import re
import html
import hashlib
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from fastapi import FastAPI, Depends, Header, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine, String, Integer, Numeric, Boolean, ForeignKey, UniqueConstraint, select, text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

ROOT = Path(__file__).resolve().parents[1]
from .storage import sqlalchemy_url, migrate_once
from .fx import fetch_nbu, convert_price
LEGACY_URL = os.getenv('DATABASE_URL', 'sqlite:///' + str(ROOT / 'beershop_v2.db'))
PERSISTENT_URL = os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL', '').strip()
# First Neon deployment copies source Render DB before serving traffic.
DB_URL = sqlalchemy_url(PERSISTENT_URL or LEGACY_URL)
engine = create_engine(DB_URL, connect_args={'check_same_thread': False, 'timeout': 30} if DB_URL.startswith('sqlite') else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase): pass
class Store(Base):
    __tablename__ = 'stores'
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(90), unique=True)
    name: Mapped[str] = mapped_column(String(180))
class Category(Base):
    __tablename__ = 'categories'
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey('stores.id'), index=True)
    slug: Mapped[str] = mapped_column(String(90))
    name: Mapped[str] = mapped_column(String(180))
    __table_args__ = (UniqueConstraint('store_id', 'slug'),)
class Product(Base):
    __tablename__ = 'products'
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey('stores.id'), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey('categories.id'))
    slug: Mapped[str] = mapped_column(String(130))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(4000), default='')
    image_url: Mapped[str] = mapped_column(String(500), default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint('store_id', 'slug'),)
class Variant(Base):
    __tablename__ = 'variants'
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey('stores.id'), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), index=True)
    sku: Mapped[str] = mapped_column(String(90))
    attributes: Mapped[str] = mapped_column(String(800), default='{}')
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    stock: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint('store_id', 'sku'),)
class ImportLog(Base):
    __tablename__ = 'import_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey('stores.id'), index=True)
    digest: Mapped[str] = mapped_column(String(64))
    added: Mapped[int] = mapped_column(Integer)
    updated: Mapped[int] = mapped_column(Integer)
    skipped: Mapped[int] = mapped_column(Integer)

class Order(Base):
    __tablename__ = 'orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey('stores.id'), index=True)
    request_key: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(180))
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str] = mapped_column(String(50))
    delivery_address: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(25), default='pending')
    payment_status: Mapped[str] = mapped_column(String(25), default='unpaid')
    payment_method: Mapped[str] = mapped_column(String(30), default='iban')
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    __table_args__ = (UniqueConstraint('store_id', 'request_key'),)
class OrderItem(Base):
    __tablename__ = 'order_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey('variants.id'))
    sku: Mapped[str] = mapped_column(String(90))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))

class FxState(Base):
    __tablename__ = 'fx_states'
    id: Mapped[int] = mapped_column(primary_key=True)
    current_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    baseline_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    effective_date: Mapped[str] = mapped_column(String(10))
    checked_at: Mapped[str] = mapped_column(String(40))


class CategoryIn(BaseModel):
    slug: str = Field(min_length=2, max_length=90, pattern=r'^[a-z0-9-]+$')
    name: str = Field(min_length=2, max_length=180)
class ProductIn(BaseModel):
    category_id: int = Field(gt=0)
    slug: str = Field(min_length=2, max_length=130, pattern=r'^[a-z0-9-]+$')
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default='', max_length=4000)
    image_url: str = Field(default='', max_length=500)
    active: bool = True
class VariantIn(BaseModel):
    sku: str = Field(min_length=2, max_length=90)
    attributes: dict[str, str] = Field(default_factory=dict)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    stock: int = Field(ge=0)
    currency: str = Field(default='UAH', pattern='^(UAH|USD)$')
    active: bool = True
class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    image_url: str | None = Field(default=None, max_length=500)
    active: bool | None = None
    category_id: int | None = Field(default=None, gt=0)

class PaymentUpdate(BaseModel):
    payment_status: str

class VariantUpdate(BaseModel):
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    stock: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern='^(UAH|USD)$')
    active: bool | None = None
class LineIn(BaseModel):
    variant_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=1000)
class CheckoutIn(BaseModel):
    request_key: str = Field(min_length=8, max_length=120)
    name: str = Field(min_length=2, max_length=180)
    email: str = Field(min_length=5, max_length=254)
    phone: str = Field(min_length=5, max_length=50)
    delivery_address: str = Field(min_length=5, max_length=500)
    items: list[LineIn] = Field(min_length=1, max_length=100)
    expected_total: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
class QuoteIn(BaseModel):
    items: list[LineIn] = Field(min_length=1, max_length=100)
class StatusIn(BaseModel):
    status: str

@asynccontextmanager
async def lifespan(app):
    if PERSISTENT_URL:
        migrate_once(Base.metadata, LEGACY_URL, engine)
    Base.metadata.create_all(engine)  # Demo bootstrap; replace with Alembic migrations in production.
    with SessionLocal.begin() as db:
        if not db.get(Store, 1):
            db.add(Store(id=1, slug='beershop', name='BeerShop Equipment'))
            db.flush()
            cats = [('couplers', 'Забірні головки'), ('taps', 'Пивні крани'), ('hoses', 'Шланги та фітинги'), ('cleaning', 'Промивка')]
            for slug, name in cats: db.add(Category(store_id=1, slug=slug, name=name))
    with SessionLocal.begin() as db:
        crm_backfill(db)
        inventory_backfill(db)
    yield
app = FastAPI(title='BeerShop Platform', version='4.4.0', lifespan=lifespan)

def session():
    with SessionLocal() as db: yield db

def auth(authorization: Annotated[str | None, Header()] = None):
    token = os.getenv('BEERSHOP_ADMIN_TOKEN', '')
    if len(token) < 32: raise HTTPException(503, 'Admin token not configured')
    provided = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    if not secrets.compare_digest(token, provided): raise HTTPException(401, 'Unauthorized')

def variant_dict(v, fx=None):
    import json
    data = {'id': v.id, 'sku': v.sku, 'attributes': json.loads(v.attributes), 'price': str(v.price), 'currency': v.currency, 'stock': v.stock, 'active': v.active}
    if fx:
        data.update({'source_price': str(v.price), 'source_currency': v.currency, 'price': str(convert_price(v.price, v.currency, fx['rate'], fx['baseline_rate'])), 'currency': 'UAH', 'rate_date': fx['effective_date']})
    return data

def product_dict(db, p, fx=None):
    return {'id': p.id, 'slug': p.slug, 'name': p.name, 'description': p.description, 'category_id': p.category_id, 'image_url': p.image_url, 'active': p.active,
            'variants': [variant_dict(v, fx=fx) for v in db.scalars(select(Variant).where(Variant.product_id == p.id, Variant.store_id == 1).order_by(Variant.id)).all()]}

def order_dict(db, o):
    lines = db.scalars(select(OrderItem).where(OrderItem.order_id == o.id)).all()
    return {'id': o.id, 'status': o.status, 'payment_status': o.payment_status, 'payment_method': o.payment_method,
            'total': str(o.total), 'currency': 'UAH', 'name': o.name, 'email': o.email, 'phone': o.phone,
            'delivery_address': o.delivery_address,
            'items': [{'sku': i.sku, 'name': i.name, 'variant_id': i.variant_id, 'quantity': i.quantity, 'unit_price': str(i.unit_price)} for i in lines]}


def fx_context():
    """NBU daily official rate; persist last good quote; keep original UAH price baseline."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        state = db.get(FxState, 1)
        if state:
            age = now - datetime.fromisoformat(state.checked_at)
            if timedelta(0) <= age < timedelta(hours=2):
                return _fx_payload(state, cached=True)
        try:
            current, effective = fetch_nbu()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if state and now - datetime.fromisoformat(state.checked_at) < timedelta(hours=96):
                return _fx_payload(state, cached=True, delayed=True)
            raise HTTPException(503, 'Курс USD/UAH НБУ тимчасово недоступний. Замовлення призупинено.') from exc
        now_text = now.isoformat()
        # Same SQL on SQLite/Postgres. Baseline is only written once, never overwritten.
        db.execute(text('INSERT INTO fx_states (id, current_rate, baseline_rate, effective_date, checked_at) '
                        'VALUES (1, :rate, :rate, :effective, :checked) ON CONFLICT (id) DO UPDATE SET '
                        'current_rate=excluded.current_rate, effective_date=excluded.effective_date, '
                        'checked_at=excluded.checked_at'),
                   {'rate': str(current), 'effective': effective, 'checked': now_text})
        db.commit()
        # SQL upsert bypasses ORM identity-map invalidation. Reload the row:
        # the quote and checkout must use the same newly persisted FX rate.
        db.expire_all()
        return _fx_payload(db.get(FxState, 1), cached=False)


def _fx_payload(state, cached=False, delayed=False):
    effective = datetime.fromisoformat(state.effective_date).date()
    today = datetime.now(timezone.utc).date()
    if not today - timedelta(days=7) <= effective <= today + timedelta(days=1):
        raise HTTPException(503, 'Курс НБУ застарілий. Замовлення призупинено.')
    return {'rate': state.current_rate, 'baseline_rate': state.baseline_rate,
            'effective_date': state.effective_date, 'checked_at': state.checked_at,
            'cached': cached, 'delayed': delayed}

@app.get('/api/fx/usd-uah')
def fx_public():
    fx = fx_context()
    return {**fx, 'rate': str(fx['rate']), 'baseline_rate': str(fx['baseline_rate']),
            'source': 'NBU', 'conversion': 'UAH / USD linked to USD-UAH rate'}

@app.post('/api/quote')
def quote_prices(data: QuoteIn, db: Session = Depends(session)):
    if len({x.variant_id for x in data.items}) != len(data.items):
        raise HTTPException(422, 'Duplicate variant IDs')
    fx = fx_context()
    ids = [x.variant_id for x in data.items]
    variants = {v.id: v for v in db.scalars(select(Variant).where(
        Variant.store_id == 1, Variant.id.in_(ids))).all()}
    visible = {p.id for p in db.scalars(select(Product).where(
        Product.store_id == 1, Product.active.is_(True),
        Product.id.in_([v.product_id for v in variants.values()]))).all()}
    total = Decimal('0.00')
    details = []
    for line in data.items:
        v = variants.get(line.variant_id)
        if not v or not v.active or v.product_id not in visible:
            raise HTTPException(404, 'Variant not found')
        if v.stock < line.quantity:
            raise HTTPException(409, 'Insufficient stock: ' + v.sku)
        unit = convert_price(v.price, v.currency, fx['rate'], fx['baseline_rate'])
        details.append({'variant_id': v.id, 'unit_price_uah': str(unit), 'quantity': line.quantity})
        total += unit * line.quantity
    return {'currency': 'UAH', 'total': str(total), 'items': details,
            'rate': str(fx['rate']), 'rate_date': fx['effective_date']}

@app.get('/api/health')
def health(): return {'status': 'ok', 'version': '4.4.0'}
@app.get('/api/categories')
def categories(db: Session = Depends(session)):
    return [{'id': c.id, 'slug': c.slug, 'name': c.name} for c in db.scalars(select(Category).where(Category.store_id == 1).order_by(Category.id)).all()]
@app.get('/api/products')
def products(q: str = Query('', max_length=100), category: str = Query('', max_length=90), page: int = Query(1, ge=1), limit: int = Query(12, ge=1, le=50), db: Session = Depends(session)):
    stmt = select(Product).where(Product.store_id == 1, Product.active.is_(True))
    if q: stmt = stmt.where(Product.name.ilike('%' + q.replace('%', r'\%').replace('_', r'\_') + '%', escape='\\'))
    if category:
        cat = db.scalar(select(Category).where(Category.store_id == 1, Category.slug == category))
        if not cat: return {'items': [], 'total': 0, 'page': page, 'limit': limit}
        stmt = stmt.where(Product.category_id == cat.id)
    count = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Product.id.desc()).offset((page - 1) * limit).limit(limit)).all()
    fx = fx_context()
    return {'items': [product_dict(db, p, fx=fx) for p in rows], 'total': count, 'page': page, 'limit': limit}
@app.get('/api/products/{slug}')
def product(slug: str, db: Session = Depends(session)):
    p = db.scalar(select(Product).where(Product.store_id == 1, Product.slug == slug, Product.active.is_(True)))
    if not p: raise HTTPException(404, 'Product not found')
    return product_dict(db, p, fx=fx_context())
@app.post('/api/admin/categories', status_code=201, dependencies=[Depends(auth)])
def add_category(data: CategoryIn, db: Session = Depends(session)):
    if db.scalar(select(Category).where(Category.store_id == 1, Category.slug == data.slug)): raise HTTPException(409, 'Category slug exists')
    c = Category(store_id=1, **data.model_dump()); db.add(c); db.commit(); db.refresh(c)
    return {'id': c.id, 'slug': c.slug, 'name': c.name}
@app.post('/api/admin/products', status_code=201, dependencies=[Depends(auth)])
def add_product(data: ProductIn, db: Session = Depends(session)):
    if not db.scalar(select(Category).where(Category.store_id == 1, Category.id == data.category_id)): raise HTTPException(422, 'Invalid category')
    if db.scalar(select(Product).where(Product.store_id == 1, Product.slug == data.slug)): raise HTTPException(409, 'Product slug exists')
    p = Product(store_id=1, **data.model_dump()); db.add(p); db.commit(); db.refresh(p)
    return product_dict(db, p)
@app.post('/api/admin/products/{product_id}/variants', status_code=201, dependencies=[Depends(auth)])
def add_variant(product_id: int, data: VariantIn, db: Session = Depends(session)):
    import json
    p = db.get(Product, product_id)
    if not p or p.store_id != 1: raise HTTPException(404, 'Product not found')
    if db.scalar(select(Variant).where(Variant.store_id == 1, Variant.sku == data.sku)): raise HTTPException(409, 'SKU exists')
    if len(json.dumps(data.attributes, ensure_ascii=False)) > 800: raise HTTPException(422, 'Attributes too long')
    v = Variant(store_id=1, product_id=p.id, sku=data.sku, attributes=json.dumps(data.attributes, ensure_ascii=False), price=data.price, currency=data.currency, stock=data.stock, active=data.active)
    db.add(v); db.commit(); db.refresh(v)
    return variant_dict(v)
@app.patch('/api/admin/variants/{variant_id}', dependencies=[Depends(auth)])
def update_variant(variant_id: int, data: VariantUpdate, db: Session = Depends(session)):
    if db.bind.dialect.name == 'sqlite': db.execute(text('BEGIN IMMEDIATE'))
    v = db.scalar(select(Variant).where(Variant.id == variant_id,Variant.store_id == 1).with_for_update())
    if not v or v.store_id != 1: raise HTTPException(404, 'Variant not found')
    for k, value in data.model_dump(exclude_unset=True).items():
        if value is None: raise HTTPException(422, 'Null not allowed')
        if k == 'stock': inventory_set_available(db,v,value,'Catalog editor: available stock')
        else: setattr(v, k, value)
    db.commit(); return variant_dict(v)
@app.post('/api/orders', status_code=201)
def checkout(data: CheckoutIn, db: Session = Depends(session)):
    fx = fx_context()
    if len({x.variant_id for x in data.items}) != len(data.items): raise HTTPException(422, 'Duplicate variant IDs')
    try:
        if db.bind.dialect.name == 'sqlite': db.execute(text('BEGIN IMMEDIATE'))
        existing = db.scalar(select(Order).where(Order.store_id == 1, Order.request_key == data.request_key))
        if existing: return order_dict(db, existing)
        ids = [x.variant_id for x in data.items]
        variants = db.scalars(select(Variant).where(Variant.store_id == 1, Variant.id.in_(ids)).order_by(Variant.id).with_for_update()).all()
        by_id = {v.id: v for v in variants}
        names = {p.id: p for p in db.scalars(select(Product).where(Product.store_id == 1, Product.id.in_([v.product_id for v in variants]))).all()}
        total = Decimal('0.00')
        for line in data.items:
            v = by_id.get(line.variant_id)
            if not v or not v.active or v.product_id not in names or not names[v.product_id].active: raise HTTPException(404, 'Variant not found')
            if v.stock < line.quantity: raise HTTPException(409, 'Insufficient stock: ' + v.sku)
            total += convert_price(v.price, v.currency, fx['rate'], fx['baseline_rate']) * line.quantity
        if data.expected_total is not None and total != data.expected_total:
            raise HTTPException(409, 'Ціна змінилася разом із курсом USD/UAH. Оновіть кошик і підтвердьте нову суму.')
        order = Order(store_id=1, request_key=data.request_key, name=data.name, email=data.email, phone=data.phone, delivery_address=data.delivery_address, total=total)
        db.add(order); db.flush()
        for line in data.items:
            v = by_id[line.variant_id]; v.stock -= line.quantity
            db.add(OrderItem(order_id=order.id, variant_id=v.id, sku=v.sku, name=names[v.product_id].name, quantity=line.quantity, unit_price=convert_price(v.price, v.currency, fx['rate'], fx['baseline_rate'])))
        crm_attach_order(db, order)
        inventory_reserve(db, order)
        db.commit()
        return order_dict(db, order)
    except Exception:
        db.rollback(); raise
@app.get('/api/admin/orders', dependencies=[Depends(auth)])
def admin_orders(db: Session = Depends(session)):
    return [order_dict(db, o) for o in db.scalars(select(Order).where(Order.store_id == 1).order_by(Order.id.desc()).limit(100)).all()]
@app.patch('/api/admin/orders/{order_id}/status', dependencies=[Depends(auth)])
def order_status(order_id: int, data: StatusIn, db: Session = Depends(session)):
    allowed = {'pending': {'confirmed', 'cancelled'}, 'confirmed': {'picking', 'cancelled'},
               'picking': {'fulfilled', 'cancelled'}, 'fulfilled': set(), 'cancelled': set()}
    try:
        if db.bind.dialect.name == 'sqlite': db.execute(text('BEGIN IMMEDIATE'))
        o = db.scalar(select(Order).where(Order.id == order_id, Order.store_id == 1).with_for_update())
        if not o: raise HTTPException(404, 'Order not found')
        if data.status == o.status: return order_dict(db, o)
        if data.status not in allowed[o.status]: raise HTTPException(409, 'Invalid status transition')
        if data.status == 'picking' and o.payment_status != 'paid':
            raise HTTPException(409, 'Комплектування потребує підтвердженої оплати')
        if data.status == 'fulfilled':
            if o.payment_status != 'paid':
                raise HTTPException(409, 'Cannot fulfill an unpaid order')
            shipment = Base.metadata.tables['order_operations']
            shipped = db.scalar(select(shipment.c.delivery_status).where(shipment.c.order_id == o.id))
            if shipped != 'delivered':
                raise HTTPException(409, 'Cannot fulfill until Nova Poshta delivery is confirmed')
            inventory_ship(db,o)
        if data.status == 'cancelled':
            shipment = db.get(OrderOperations, o.id)
            if o.payment_status == 'paid' or (shipment and shipment.tracking_number):
                raise HTTPException(409, 'Оплачене замовлення або замовлення з ТТН потребує окремої перевірки повернення. Автоматичне скасування заборонено.')
            inventory_release(db,o)
        previous = o.status
        o.status = data.status
        db.add(OperationsAudit(order_id=o.id, event='order_status_changed',
                               at=datetime.now(timezone.utc).isoformat(), note=previous+' -> '+data.status))
        db.commit(); return order_dict(db, o)
    except Exception:
        db.rollback(); raise
@app.get('/api/admin/orders/{order_id}/invoice', dependencies=[Depends(auth)])
def invoice(order_id: int, db: Session = Depends(session)):
    o = db.scalar(select(Order).where(Order.store_id == 1, Order.id == order_id))
    if not o: raise HTTPException(404, 'Order not found')
    iban = os.getenv('BEERSHOP_IBAN', '').strip(); payee = os.getenv('BEERSHOP_PAYEE', '').strip()
    if not iban or not payee: raise HTTPException(503, 'Bank details not configured')
    return {'invoice_number': f'BS-{o.id:06}', 'currency': 'UAH', 'total': str(o.total), 'iban': iban, 'payee': payee,
            'purpose': f'Оплата замовлення BS-{o.id:06}', 'payment_status': o.payment_status,
            'note': 'Оплату звіряє менеджер за банківською випискою. Це не фіскальний документ.'}
@app.get('/')
def index(): return FileResponse(ROOT / 'web' / 'index.html')

@app.get('/manage')
def manage(): return FileResponse(ROOT / 'web' / 'manage.html')

@app.get('/api/admin/products', dependencies=[Depends(auth)])
def admin_products(db: Session = Depends(session)):
    return [product_dict(db, p) for p in db.scalars(select(Product).where(Product.store_id == 1).order_by(Product.id.desc()).limit(5000)).all()]

@app.patch('/api/admin/products/{product_id}', dependencies=[Depends(auth)])
def edit_product(product_id: int, data: ProductUpdate, db: Session = Depends(session)):
    p = db.get(Product, product_id)
    if not p or p.store_id != 1: raise HTTPException(404, 'Product not found')
    for key, val in data.model_dump(exclude_unset=True).items():
        if val is None: raise HTTPException(422, 'Null not allowed')
        setattr(p, key, val)
    if not db.scalar(select(Category).where(Category.id == p.category_id, Category.store_id == 1)): raise HTTPException(422, 'Invalid category')
    db.commit()
    return product_dict(db, p)

@app.patch('/api/admin/orders/{order_id}/payment', dependencies=[Depends(auth)])
def set_payment(order_id: int, data: PaymentUpdate, db: Session = Depends(session)):
    if data.payment_status not in {'unpaid', 'paid'}: raise HTTPException(422, 'Invalid payment status')
    o = db.scalar(select(Order).where(Order.id == order_id, Order.store_id == 1))
    if not o: raise HTTPException(404, 'Order not found')
    # No legacy shortcut for marking an order paid without a bank statement.
    if data.payment_status != o.payment_status:
        raise HTTPException(409, 'Use /api/admin/ops/orders/{id}/bank-confirm after verifying an actual bank statement. Refunds require a separate controlled workflow.')
    return order_dict(db, o)

@app.post('/api/admin/import/preview', dependencies=[Depends(auth)])
async def import_preview(file: UploadFile = File(...)):
    raw = await file.read(2_000_001)
    if len(raw) > 2_000_000: raise HTTPException(413, 'Maximum CSV size is 2 MB')
    try:
        content = raw.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))
        required = {'category_slug','product_slug','product_name','sku','price','stock'}
        if not required.issubset(set(reader.fieldnames or [])):
            raise HTTPException(422, 'Required columns: ' + ', '.join(sorted(required)))
        rows = []
        for i, r in enumerate(reader, 2):
            if i > 501: raise HTTPException(422, 'Maximum 500 rows')
            if not r['sku'] or not r['product_slug']: raise HTTPException(422, f'Missing SKU or slug at row {i}')
            price = Decimal(r['price']); stock = int(r['stock'])
            if not price.is_finite() or price < 0 or price.as_tuple().exponent < -2 or stock < 0: raise ValueError('Invalid price or stock')
            rows.append({'row': i, 'sku': r['sku'], 'name': r['product_name'], 'price': str(price), 'stock': stock, 'category': r['category_slug']})
        return {'count': len(rows), 'preview': rows[:20], 'note': 'Preview only. No products were imported.'}
    except (UnicodeDecodeError, ValueError, KeyError, csv.Error) as e:
        raise HTTPException(422, 'Invalid CSV: ' + str(e))


# Prom.ua CSV import: missing rows are never deleted. Unknown availability is inactive.
def _slug(value: str) -> str:
    value = str(value).strip().lower()
    return re.sub(r'[^a-z0-9-]', '-', value).strip('-')[:110] or 'item'

def _plain(value: str) -> str:
    return html.unescape(re.sub(r'<[^>]+>', ' ', value or '')).strip()[:4000]

def _prom_parse(raw: bytes):
    if len(raw) > 5_000_000: raise HTTPException(413, 'CSV exceeds 5 MB')
    try: content = raw.decode('utf-8-sig')
    except UnicodeDecodeError: raise HTTPException(422, 'Expected UTF-8 CSV')
    reader = csv.DictReader(io.StringIO(content))
    required = {'Уникальный_идентификатор', 'Название_позиции', 'Цена', 'Валюта', 'Наличие'}
    if not required.issubset(reader.fieldnames or []): raise HTTPException(422, 'This is not a compatible Prom.ua CSV')
    parsed = []; seen = set(); used_skus=set()
    for line, row in enumerate(reader, 2):
        if line > 10002: raise HTTPException(422, 'Maximum 10000 rows')
        uid = (row.get('Уникальный_идентификатор') or '').strip()
        if not uid or uid in seen: raise HTTPException(422, f'Duplicate or empty Prom ID at row {line}')
        seen.add(uid)
        currency=(row.get('Валюта') or '').strip()
        if currency not in ('UAH','USD'): raise HTTPException(422, f'Unsupported currency at row {line}')
        try: price = Decimal((row.get('Цена') or '').replace(',', '.').strip())
        except Exception: raise HTTPException(422, f'Invalid price at row {line}')
        if not price.is_finite() or price < 0 or price.as_tuple().exponent < -2: raise HTTPException(422, f'Invalid price at row {line}')
        name = (row.get('Название_позиции_укр') or row.get('Название_позиции') or '').strip()[:200]
        if len(name) < 2: raise HTTPException(422, f'Missing name at row {line}')
        catid = (row.get('Номер_группы') or '').strip()
        catname = (row.get('Название_группы') or 'Інше').strip()[:180]
        if not catid: catid = 'other'
        image = (row.get('Ссылка_изображения') or '').split(',')[0].strip()[:500]
        if image and urlparse(image).scheme not in ('http','https'): image = ''
        availability = (row.get('Наличие') or '').strip()
        # Prom export + means in stock, - unavailable; 0 and & conservatively inactive.
        available = availability == '+'
        sku = ((row.get('Код_товара') or '').strip() or ('PROM-' + uid))[:90]
        if sku in used_skus: sku = ('PROM-' + uid)[:90]
        used_skus.add(sku)
        parsed.append({'uid':uid,'slug':'prom-'+_slug(uid),'sku':sku[:90], 'name':name,
          'description':_plain(row.get('Описание_укр') or row.get('Описание') or ''),
          'price':price,'currency':currency,'image_url':image,'cat_slug':'prom-cat-'+_slug(catid),
          'cat_name':catname or 'Інше','active':available,'availability':availability})
    return parsed

@app.post('/api/admin/prom/preview', dependencies=[Depends(auth)])
async def prom_preview(file: UploadFile = File(...), db: Session = Depends(session)):
    raw = await file.read(5_000_001)
    rows = _prom_parse(raw)
    existing = {p.slug for p in db.scalars(select(Product).where(Product.store_id == 1)).all()}
    return {'total':len(rows),'new':sum(r['slug'] not in existing for r in rows),
      'existing':sum(r['slug'] in existing for r in rows),
      'unavailable_or_unknown':sum(not r['active'] for r in rows),
      'currencies':{'UAH':sum(r['currency']=='UAH' for r in rows),'USD':sum(r['currency']=='USD' for r in rows)},
      'sample':[{'name':r['name'],'sku':r['sku'],'price':str(r['price']),'currency':r['currency'],'availability':r['availability']} for r in rows[:8]],
      'warning':'USD prices are kept in USD and blocked from checkout until converted/approved in UAH. Prom availability other than + is imported as hidden and stock 0. Review before selling. Import never deletes products.'}

@app.post('/api/admin/prom/apply', dependencies=[Depends(auth)])
async def prom_apply(file: UploadFile = File(...), db: Session = Depends(session)):
    raw = await file.read(5_000_001)
    rows = _prom_parse(raw)
    digest = hashlib.sha256(raw).hexdigest()
    # This is an explicitly requested one-time import. Future changes are managed in the admin UI.
    added=updated=skipped=0
    try:
        cats = {c.slug:c for c in db.scalars(select(Category).where(Category.store_id == 1)).all()}
        existing = {p.slug:p for p in db.scalars(select(Product).where(Product.store_id == 1)).all()}
        skus = {v.sku:v for v in db.scalars(select(Variant).where(Variant.store_id == 1)).all()}
        for r in rows:
            cat = cats.get(r['cat_slug'])
            if cat is None:
                cat = Category(store_id=1,slug=r['cat_slug'],name=r['cat_name']); db.add(cat); db.flush(); cats[cat.slug]=cat
            p = existing.get(r['slug'])
            if p is None:
                p = Product(store_id=1,category_id=cat.id,slug=r['slug'],name=r['name'],description=r['description'],image_url=r['image_url'],active=r['active'])
                db.add(p); db.flush(); existing[p.slug]=p; added+=1
            else:
                p.category_id=cat.id;p.name=r['name'];p.description=r['description'];p.image_url=r['image_url'];p.active=r['active'];updated+=1
            sku = r['sku']
            v = skus.get(sku)
            if v is not None and v.product_id != p.id:
                # Never overwrite a different product sharing a SKU.
                raise HTTPException(409, 'SKU collision: '+sku)
            if v is None:
                v=Variant(store_id=1,product_id=p.id,sku=sku,price=r['price'],currency=r['currency'],stock=0,active=r['active'],attributes='{}');db.add(v);skus[sku]=v
            else:
                v.price=r['price'];v.currency=r['currency'];v.active=r['active']
            # CSV does not contain a trustworthy numeric stock quantity: keep existing stock.
        db.add(ImportLog(store_id=1,digest=digest,added=added,updated=updated,skipped=skipped))
        db.commit()
    except Exception:
        db.rollback();raise
    return {'added':added,'updated':updated,'skipped':skipped,'message':'Imported. Stock defaults to 0; set stock in admin before selling.'}

@app.get('/api/admin/import/history', dependencies=[Depends(auth)])
def import_history(db: Session = Depends(session)):
    return [{'id':x.id,'digest':x.digest,'added':x.added,'updated':x.updated,'skipped':x.skipped} for x in db.scalars(select(ImportLog).where(ImportLog.store_id==1).order_by(ImportLog.id.desc()).limit(30)).all()]

@app.post('/api/admin/products/{product_id}/photo', dependencies=[Depends(auth)])
async def upload_photo(product_id: int, file: UploadFile = File(...), db: Session = Depends(session)):
    p=db.scalar(select(Product).where(Product.id==product_id,Product.store_id==1))
    if not p: raise HTTPException(404,'Product not found')
    raw=await file.read(3_000_001)
    if len(raw)>3_000_000: raise HTTPException(413,'Maximum image size 3 MB')
    # Validate actual image signature, not just user-provided content-type.
    if raw.startswith(b'\xff\xd8\xff'): ext='jpg'
    elif raw.startswith(b'\x89PNG\r\n\x1a\n'): ext='png'
    elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP': ext='webp'
    else: raise HTTPException(422,'Only JPEG, PNG and WebP supported')
    directory=ROOT/'uploads';directory.mkdir(exist_ok=True)
    filename=f'{uuid4().hex}.{ext}'
    (directory/filename).write_bytes(raw)
    p.image_url='/uploads/'+filename
    db.commit()
    return {'image_url':p.image_url}

@app.get('/uploads/{filename}')
def uploaded_image(filename: str):
    if not re.fullmatch(r'[0-9a-f]{32}\.(jpg|png|webp)',filename): raise HTTPException(404)
    f=ROOT/'uploads'/filename
    if not f.is_file(): raise HTTPException(404)
    return FileResponse(f,headers={'X-Content-Type-Options':'nosniff'})


# Bulk Admin 3.1: scoped SKU catalog, preview-first, atomic optimistic editing.
# Existing inventory is not changed when these endpoints are added.
class BulkChange(BaseModel):
    product_id: int = Field(gt=0)
    variant_id: int = Field(gt=0)
    expected_product_active: bool
    expected_variant_active: bool
    expected_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    expected_currency: Literal['UAH', 'USD']
    expected_stock: int = Field(ge=0)
    product_active: bool | None = None
    variant_active: bool | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: Literal['UAH', 'USD'] | None = None
    stock: int | None = Field(default=None, ge=0, le=1000000)

class BulkRequest(BaseModel):
    changes: list[BulkChange] = Field(min_length=1, max_length=100)

@app.get('/api/admin/bulk/catalog', dependencies=[Depends(auth)])
def bulk_catalog(q: str = Query('', max_length=100), category_id: int | None = Query(None, gt=0),
                 status: Literal['all', 'published', 'hidden'] = 'all',
                 currency: Literal['all', 'UAH', 'USD'] = 'all', page: int = Query(1, ge=1),
                 limit: int = Query(25, ge=1, le=100), db: Session = Depends(session)):
    stmt = select(Product, Variant, Category.name).join(Variant, Variant.product_id == Product.id).join(
        Category, Category.id == Product.category_id).where(
        Product.store_id == 1, Variant.store_id == 1, Category.store_id == 1)
    if q:
        safe = q.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')
        stmt = stmt.where(Product.name.ilike('%' + safe + '%', escape='\\') |
                          Variant.sku.ilike('%' + safe + '%', escape='\\'))
    if category_id is not None: stmt = stmt.where(Product.category_id == category_id)
    if status == 'published': stmt = stmt.where(Product.active.is_(True), Variant.active.is_(True))
    if status == 'hidden': stmt = stmt.where((Product.active.is_(False)) | (Variant.active.is_(False)))
    if currency != 'all': stmt = stmt.where(Variant.currency == currency)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(Product.id.desc(), Variant.id).offset((page-1)*limit).limit(limit)).all()
    items = [{'product_id': p.id, 'variant_id': v.id, 'name': p.name,
              'category_id': p.category_id, 'category': category_name,
              'sku': v.sku, 'price': str(v.price), 'currency': v.currency,
              'stock': v.stock, 'product_active': p.active, 'variant_active': v.active}
             for p, v, category_name in rows]
    return {'items': items, 'total': total, 'page': page, 'limit': limit}

def _bulk_process(payload: BulkRequest, db: Session, apply: bool):
    entries = payload.changes
    ids = [e.variant_id for e in entries]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, 'Duplicate variant IDs in batch')
    # SQLite single-writer lock and PostgreSQL row-level locks serialize updates.
    if apply and db.bind.dialect.name == 'sqlite':
        db.execute(text('BEGIN IMMEDIATE'))
    vstmt = select(Variant).where(Variant.store_id == 1, Variant.id.in_(ids)).order_by(Variant.id)
    if apply: vstmt = vstmt.with_for_update()
    variants = db.scalars(vstmt).all()
    by_variant = {v.id: v for v in variants}
    product_ids = sorted({e.product_id for e in entries})
    pstmt = select(Product).where(Product.store_id == 1, Product.id.in_(product_ids)).order_by(Product.id)
    if apply: pstmt = pstmt.with_for_update()
    products = db.scalars(pstmt).all()
    by_product = {p.id: p for p in products}
    changes = []
    requested_product_active = {}
    for e in entries:
        p, v = by_product.get(e.product_id), by_variant.get(e.variant_id)
        if p is None or v is None or v.product_id != p.id:
            raise HTTPException(404, f'Invalid product/variant pair: {e.product_id}/{e.variant_id}')
        if (p.active != e.expected_product_active or v.active != e.expected_variant_active or
            v.price != e.expected_price or v.currency != e.expected_currency or
            v.stock != e.expected_stock):
            raise HTTPException(409, f'SKU {v.sku} changed since loading. Refresh and review again.')
        if e.currency is not None and e.currency != v.currency and e.price is None:
            raise HTTPException(422, f'Changing currency for SKU {v.sku} requires a verified new price')
        if e.product_active is not None:
            previous = requested_product_active.setdefault(p.id, e.product_active)
            if previous != e.product_active:
                raise HTTPException(422, f'Conflicting visibility for product {p.id}')
        fields = {}
        for field in ('product_active', 'variant_active', 'price', 'currency', 'stock'):
            proposed = getattr(e, field)
            if proposed is None: continue
            current = (p.active if field == 'product_active' else
                       v.active if field == 'variant_active' else getattr(v, field))
            if proposed != current:
                fields[field] = {'from': str(current) if field == 'price' else current,
                                 'to': str(proposed) if field == 'price' else proposed}
        if not fields:
            raise HTTPException(422, f'No actual changes for SKU {v.sku}')
        changes.append({'product_id': p.id, 'variant_id': v.id, 'sku': v.sku, 'fields': fields})
    # Validate all snapshots first, mutate the batch only after validation.
    if apply:
        for entry in entries:
            p, v = by_product[entry.product_id], by_variant[entry.variant_id]
            for field in ('product_active', 'variant_active', 'price', 'currency', 'stock'):
                value = getattr(entry, field)
                if value is not None:
                    if field == 'stock':
                        inventory_set_available(db,v,value,'Bulk editor: available stock')
                        continue
                    target = p if field == 'product_active' else v
                    setattr(target, 'active' if field in ('product_active', 'variant_active') else field, value)
        db.commit()
    return {'count': len(changes), 'applied': apply, 'changes': changes}

@app.post('/api/admin/bulk/preview', dependencies=[Depends(auth)])
def bulk_preview(payload: BulkRequest, db: Session = Depends(session)):
    return _bulk_process(payload, db, False)

@app.post('/api/admin/bulk/apply', dependencies=[Depends(auth)])
def bulk_apply(payload: BulkRequest, db: Session = Depends(session)):
    try:
        return _bulk_process(payload, db, True)
    except Exception:
        db.rollback()
        raise

@app.get('/assets/admin-bulk.js')
def bulk_js():
    return FileResponse(ROOT / 'web' / 'admin-bulk.js', media_type='application/javascript',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

@app.get('/assets/admin-bulk.css')
def bulk_css():
    return FileResponse(ROOT / 'web' / 'admin-bulk.css', media_type='text/css',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


# Mobile catalog manager 3.2: paginated product discovery and category renames.
# Deploying this module does not change preexisting product data.
class CategoryRename(BaseModel):
    name: str = Field(min_length=2, max_length=180)

@app.get('/api/admin/catalog32', dependencies=[Depends(auth)])
def mobile_catalog(q: str = Query('', max_length=100),
                   category_id: int | None = Query(None, gt=0),
                   page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50),
                   db: Session = Depends(session)):
    stmt = select(Product).where(Product.store_id == 1)
    if q:
        safe = q.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')
        sku_ids = select(Variant.product_id).where(Variant.store_id == 1,
                          Variant.sku.ilike('%' + safe + '%', escape='\\'))
        stmt = stmt.where(Product.name.ilike('%' + safe + '%', escape='\\') |
                          Product.id.in_(sku_ids))
    if category_id is not None: stmt = stmt.where(Product.category_id == category_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Product.id.desc()).offset((page-1)*limit).limit(limit)).all()
    return {'total':total,'page':page,'limit':limit,'items':[
        {'id':p.id,'name':p.name,'image_url':p.image_url,'category_id':p.category_id,
         'active':p.active,'variants':len(db.scalars(select(Variant.id).where(
            Variant.store_id == 1, Variant.product_id == p.id)).all())}
        for p in rows]}

@app.get('/api/admin/catalog32/{product_id}', dependencies=[Depends(auth)])
def mobile_product(product_id: int, db: Session = Depends(session)):
    p = db.scalar(select(Product).where(Product.id == product_id, Product.store_id == 1))
    if p is None: raise HTTPException(404, 'Product not found')
    return product_dict(db, p)

@app.get('/api/admin/categories32', dependencies=[Depends(auth)])
def mobile_categories(db: Session = Depends(session)):
    cats = db.scalars(select(Category).where(Category.store_id == 1).order_by(Category.name)).all()
    return [{'id':c.id,'name':c.name,'slug':c.slug,
             'products':db.scalar(select(func.count()).select_from(Product).where(
                 Product.store_id == 1, Product.category_id == c.id))} for c in cats]

@app.patch('/api/admin/categories32/{category_id}', dependencies=[Depends(auth)])
def rename_category(category_id: int, payload: CategoryRename, db: Session = Depends(session)):
    c = db.scalar(select(Category).where(Category.id == category_id, Category.store_id == 1))
    if c is None: raise HTTPException(404, 'Category not found')
    name = payload.name.strip()
    if len(name) < 2: raise HTTPException(422, 'Category name is too short')
    c.name = name
    db.commit()
    return {'id':c.id,'slug':c.slug,'name':c.name}

@app.get('/assets/admin-catalog32.js')
def catalog32_js():
    return FileResponse(ROOT / 'web' / 'admin-catalog32.js', media_type='application/javascript',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@app.get('/assets/admin-catalog32.css')
def catalog32_css():
    return FileResponse(ROOT / 'web' / 'admin-catalog32.css', media_type='text/css',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

# Admin-only JSON snapshot. Contains customer PII; requires bearer token.
@app.get('/api/admin/maintenance/report', dependencies=[Depends(auth)])
def maintenance_report(db: Session = Depends(session)):
    names = ('stores', 'categories', 'products', 'variants', 'import_logs', 'orders', 'order_items')
    counts = {name: db.scalar(select(func.count()).select_from(Base.metadata.tables[name])) for name in names}
    currencies = {currency: count for currency, count in db.execute(
        select(Variant.currency, func.count()).group_by(Variant.currency)).all()}
    return {'storage': 'neon' if PERSISTENT_URL else 'render',
            'counts': counts, 'currencies': currencies,
            'products_hidden': db.scalar(select(func.count()).select_from(Product).where(Product.active.is_(False))),
            'stock_positive': db.scalar(select(func.count()).select_from(Variant).where(Variant.stock > 0))}

@app.get('/api/admin/maintenance/export', dependencies=[Depends(auth)])
def maintenance_export(db: Session = Depends(session)):
    snapshot = {'format': 'beershop-snapshot-v1',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'tables': {t.name: [dict(r._mapping) for r in db.execute(select(t)).all()]
                           for t in Base.metadata.sorted_tables}}
    output = json.dumps(snapshot, default=str, ensure_ascii=False).encode('utf-8')
    return Response(content=output, media_type='application/json; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="beershop-private-backup.json"',
                             'Cache-Control': 'private, no-store',
                             'X-Content-Type-Options': 'nosniff',
                             'X-Robots-Tag': 'noindex, nofollow'})

@app.get('/assets/admin-backup.js')
def backup_js():
    return FileResponse(ROOT / 'web' / 'admin-backup.js', media_type='application/javascript',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@app.get('/assets/store-fx.js')
def store_fx_js():
    return FileResponse(ROOT / 'web' / 'store-fx.js', media_type='application/javascript',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


# Register audited operations and their tables before FastAPI startup creates tables.
from .ops40 import setup_ops
OrderOperations, OperationsAudit = setup_ops(app, Base, Order, OrderItem, auth, session,
                                             lambda db,order: inventory_ship(db,order))
from .connectors41 import setup_connectors41
setup_connectors41(app, Base, Order, OrderItem, OrderOperations, OperationsAudit, auth, session,
                   lambda db,order: inventory_ship(db,order))
from .inventory import setup_inventory
(inventory_reserve,inventory_release,inventory_ship,inventory_backfill,
 inventory_order,inventory_set_available) = setup_inventory(
    app,Base,Order,OrderItem,Variant,Product,OrderOperations,OperationsAudit,auth,session)
from .crm import setup_crm
crm_attach_order, crm_backfill = setup_crm(app, Base, Order, OrderItem, OrderOperations,
                                          OperationsAudit, auth, session, order_dict,inventory_order,inventory_backfill)

@app.middleware('http')
async def private_crm_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(('/api/admin/crm','/api/admin/inventory','/crm/')) or request.url.path in {'/crm','/crm-legacy'}:
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.get('/crm')
@app.get('/crm/', include_in_schema=False)
def crm_page():
    index = ROOT / 'web' / 'crm-dist' / 'index.html'
    if not index.is_file():
        raise HTTPException(503, 'CRM frontend is not built. Run npm ci and npm run build in frontend/.')
    return FileResponse(index)

app.mount('/crm/assets', StaticFiles(directory=ROOT / 'web' / 'crm-dist' / 'assets', check_dir=False), name='crm-assets')

@app.get('/crm-legacy', include_in_schema=False)
def crm_legacy_page():
    return FileResponse(ROOT / 'web' / 'crm.html')

@app.get('/assets/crm.js')
def crm_js():
    return FileResponse(ROOT / 'web' / 'crm.js', media_type='application/javascript', headers={'Cache-Control':'no-store'})

@app.get('/assets/crm.css')
def crm_css():
    return FileResponse(ROOT / 'web' / 'crm.css', media_type='text/css', headers={'Cache-Control':'no-store'})

@app.get('/assets/crm-icons/{name}')
def crm_icon(name: str):
    allowed = {'store.svg','log-out.svg','refresh-cw.svg','plus.svg','search.svg',
               'arrow-left.svg','arrow-right.svg','x.svg','clipboard-list.svg','users.svg','pencil.svg'}
    if name not in allowed:
        raise HTTPException(404, 'Not found')
    return FileResponse(ROOT / 'web' / 'crm-icons' / name, media_type='image/svg+xml')

@app.get('/assets/ops40.js')
def ops40_js():
    return FileResponse(ROOT / 'web' / 'ops40.js', media_type='application/javascript',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

@app.get('/assets/connectors41.js')
def connector_js():
    return FileResponse(ROOT / 'web' / 'connectors41.js', media_type='application/javascript',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

