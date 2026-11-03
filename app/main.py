"""BeerShop v2: equipment catalog, SKUs, inventory-safe checkout, IBAN invoices.
Demo single-store scope; do not deploy without production hardening.
"""
import os
import csv
import io
import secrets
import re
import html
import hashlib
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import uuid4
from fastapi import FastAPI, Depends, Header, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine, String, Integer, Numeric, Boolean, ForeignKey, UniqueConstraint, select, text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

ROOT = Path(__file__).resolve().parents[1]
DB_URL = os.getenv('DATABASE_URL', 'sqlite:///' + str(ROOT / 'beershop_v2.db'))
# Render provides a postgresql:// URL; use the installed psycopg v3 SQLAlchemy driver.
if DB_URL.startswith('postgres://'):
    DB_URL = 'postgresql+psycopg://' + DB_URL[len('postgres://'):]
elif DB_URL.startswith('postgresql://'):
    DB_URL = 'postgresql+psycopg://' + DB_URL[len('postgresql://'):]
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
class StatusIn(BaseModel):
    status: str

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)  # Demo bootstrap; replace with Alembic migrations in production.
    with SessionLocal.begin() as db:
        if not db.get(Store, 1):
            db.add(Store(id=1, slug='beershop', name='BeerShop Equipment'))
            db.flush()
            cats = [('couplers', 'Забірні головки'), ('taps', 'Пивні крани'), ('hoses', 'Шланги та фітинги'), ('cleaning', 'Промивка')]
            for slug, name in cats: db.add(Category(store_id=1, slug=slug, name=name))
    yield
app = FastAPI(title='BeerShop Platform', version='2.1.0', lifespan=lifespan)

def session():
    with SessionLocal() as db: yield db

def auth(authorization: Annotated[str | None, Header()] = None):
    token = os.getenv('BEERSHOP_ADMIN_TOKEN', '')
    if len(token) < 32: raise HTTPException(503, 'Admin token not configured')
    provided = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    if not secrets.compare_digest(token, provided): raise HTTPException(401, 'Unauthorized')

def variant_dict(v):
    import json
    return {'id': v.id, 'sku': v.sku, 'attributes': json.loads(v.attributes), 'price': str(v.price), 'currency': v.currency, 'stock': v.stock, 'active': v.active}

def product_dict(db, p):
    return {'id': p.id, 'slug': p.slug, 'name': p.name, 'description': p.description, 'category_id': p.category_id, 'image_url': p.image_url, 'active': p.active,
            'variants': [variant_dict(v) for v in db.scalars(select(Variant).where(Variant.product_id == p.id, Variant.store_id == 1).order_by(Variant.id)).all()]}

def order_dict(db, o):
    lines = db.scalars(select(OrderItem).where(OrderItem.order_id == o.id)).all()
    return {'id': o.id, 'status': o.status, 'payment_status': o.payment_status, 'payment_method': o.payment_method,
            'total': str(o.total), 'currency': 'UAH', 'name': o.name, 'email': o.email, 'phone': o.phone,
            'delivery_address': o.delivery_address,
            'items': [{'sku': i.sku, 'name': i.name, 'variant_id': i.variant_id, 'quantity': i.quantity, 'unit_price': str(i.unit_price)} for i in lines]}

@app.get('/api/health')
def health(): return {'status': 'ok', 'version': '2.1.0'}
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
    return {'items': [product_dict(db, p) for p in rows], 'total': count, 'page': page, 'limit': limit}
@app.get('/api/products/{slug}')
def product(slug: str, db: Session = Depends(session)):
    p = db.scalar(select(Product).where(Product.store_id == 1, Product.slug == slug, Product.active.is_(True)))
    if not p: raise HTTPException(404, 'Product not found')
    return product_dict(db, p)
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
    v = db.get(Variant, variant_id)
    if not v or v.store_id != 1: raise HTTPException(404, 'Variant not found')
    for k, value in data.model_dump(exclude_unset=True).items():
        if value is None: raise HTTPException(422, 'Null not allowed')
        setattr(v, k, value)
    db.commit(); return variant_dict(v)
@app.post('/api/orders', status_code=201)
def checkout(data: CheckoutIn, db: Session = Depends(session)):
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
            if v.currency != 'UAH': raise HTTPException(409, 'USD-priced items require a confirmed UAH price before checkout')
            if v.stock < line.quantity: raise HTTPException(409, 'Insufficient stock: ' + v.sku)
            total += v.price * line.quantity
        order = Order(store_id=1, request_key=data.request_key, name=data.name, email=data.email, phone=data.phone, delivery_address=data.delivery_address, total=total)
        db.add(order); db.flush()
        for line in data.items:
            v = by_id[line.variant_id]; v.stock -= line.quantity
            db.add(OrderItem(order_id=order.id, variant_id=v.id, sku=v.sku, name=names[v.product_id].name, quantity=line.quantity, unit_price=v.price))
        db.commit()
        return order_dict(db, order)
    except Exception:
        db.rollback(); raise
@app.get('/api/admin/orders', dependencies=[Depends(auth)])
def admin_orders(db: Session = Depends(session)):
    return [order_dict(db, o) for o in db.scalars(select(Order).where(Order.store_id == 1).order_by(Order.id.desc()).limit(100)).all()]
@app.patch('/api/admin/orders/{order_id}/status', dependencies=[Depends(auth)])
def order_status(order_id: int, data: StatusIn, db: Session = Depends(session)):
    allowed = {'pending': {'confirmed', 'cancelled'}, 'confirmed': {'fulfilled', 'cancelled'}, 'fulfilled': set(), 'cancelled': set()}
    try:
        if db.bind.dialect.name == 'sqlite': db.execute(text('BEGIN IMMEDIATE'))
        o = db.scalar(select(Order).where(Order.id == order_id, Order.store_id == 1).with_for_update())
        if not o: raise HTTPException(404, 'Order not found')
        if data.status == o.status: return order_dict(db, o)
        if data.status not in allowed[o.status]: raise HTTPException(409, 'Invalid status transition')
        if data.status == 'cancelled':
            for line in db.scalars(select(OrderItem).where(OrderItem.order_id == o.id)).all():
                v = db.get(Variant, line.variant_id)
                if v: v.stock += line.quantity
        o.status = data.status; db.commit(); return order_dict(db, o)
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
    if o.status == 'cancelled' and data.payment_status == 'paid': raise HTTPException(409, 'Cancelled order')
    o.payment_status = data.payment_status
    db.commit()
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
