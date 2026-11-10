"""Opt-in, staging-only identity pilot for BeerShop v9.

Password: Argon2id. TOTP: RFC 6238 with single-use counters.
TOTP seeds: Fernet-encrypted with a key kept *outside* the database.
Sessions: short-lived, SHA-256 hashed random bearer tokens, revocable.

WARNING: These sessions do NOT grant access to existing legacy /api/admin
endpoints. Those still use the shared admin token until a separately audited
migration. Do not activate this pilot on production.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
from datetime import datetime, timezone
from urllib.parse import quote

from argon2 import PasswordHasher, exceptions as argon_errors
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column, Session

ROLES = ('owner', 'catalog', 'operations', 'auditor')
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
SESSION_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60
MAX_FAILURES = 5


def is_enabled():
    return os.getenv('BEERSHOP_V9_STAFF_AUTH', '') == '1'


def require_staging():
    if not is_enabled():
        raise HTTPException(503, 'Staff identity pilot disabled')
    key = os.getenv('BEERSHOP_V9_MFA_KEY', '')
    try:
        # Fernet requires a random 32-byte key, base64url-encoded. Do not
        # reuse BEERSHOP_ADMIN_TOKEN as the encryption key.
        if len(base64.urlsafe_b64decode(key.encode())) != 32:
            raise ValueError('Wrong key length')
        return Fernet(key.encode())
    except (ValueError, TypeError, base64.binascii.Error):
        raise HTTPException(503, 'Staging staff MFA key not configured') from None


def new_totp_seed():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def totp_at(seed, step):
    key = base64.b32decode(seed + '=' * (-len(seed) % 8), casefold=True)
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff
    return f'{number % 1_000_000:06d}'


def matching_counter(seed, code, now=None, after=-1):
    if not re.fullmatch(r'\d{6}', code or ''):
        return None
    current = int((time.time() if now is None else now) // 30)
    # Only a ±1 step allowance for clock drift and reject replay.
    for candidate in (current, current - 1, current + 1):
        if candidate > after and candidate >= 0 and hmac.compare_digest(totp_at(seed, candidate), code):
            return candidate
    return None


class LoginIn(BaseModel):
    username: str = Field(pattern=r'^[a-z][a-z0-9_.-]{2,31}$')
    password: str = Field(min_length=1, max_length=256)
    otp: str = Field(pattern=r'^\d{6}$')


class CreateUserIn(BaseModel):
    username: str = Field(pattern=r'^[a-z][a-z0-9_.-]{2,31}$')
    password: str = Field(min_length=14, max_length=128)
    role: str


def setup_staff_auth(app, Base, session, audit_provider):
    class StaffUser(Base):
        __tablename__ = 'v9_staff_users'
        id: Mapped[int] = mapped_column(primary_key=True)
        username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
        password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
        role: Mapped[str] = mapped_column(String(20), nullable=False)
        mfa_ciphertext: Mapped[str] = mapped_column(String(512), nullable=False)
        active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
        failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
        locked_until: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
        last_totp_counter: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)

    class StaffSession(Base):
        __tablename__ = 'v9_staff_sessions'
        id: Mapped[int] = mapped_column(primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey('v9_staff_users.id'), nullable=False, index=True)
        token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
        created_at: Mapped[int] = mapped_column(Integer, nullable=False)
        expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
        revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    router = APIRouter(prefix='/api/admin/v9/staff', tags=['v9-staff-pilot'])
    def encrypt_seed(seed):
        return require_staging().encrypt(seed.encode('ascii')).decode('ascii')

    def create_user(db: Session, username: str, password: str, role: str):
        if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,31}', username):
            raise ValueError('Invalid username')
        if role not in ROLES or len(password) < 14 or len(password) > 128:
            raise ValueError('Invalid role or password')
        if db.scalar(select(StaffUser.id).where(StaffUser.username == username)) is not None:
            raise ValueError('Username already exists')
        seed = new_totp_seed()
        user = StaffUser(username=username, password_hash=PASSWORD_HASHER.hash(password),
                         role=role, mfa_ciphertext=encrypt_seed(seed),
                         failed_attempts=0, locked_until=0, last_totp_counter=-1)
        db.add(user)
        db.commit()
        db.refresh(user)
        uri = f'otpauth://totp/BeerShop-v9:{quote(username)}?secret={seed}&issuer=BeerShop-v9&digits=6&period=30'
        return user, uri

    def staff_session(authorization: str | None = Header(default=None),
                      db: Session = Depends(session)):
        require_staging()
        token = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
        if len(token) < 32 or len(token) > 256:
            raise HTTPException(401, 'Unauthorized')
        digest = hashlib.sha256(token.encode()).hexdigest()
        item = db.scalar(select(StaffSession).where(StaffSession.token_hash == digest))
        now = int(time.time())
        if not item or item.revoked or item.expires_at <= now:
            raise HTTPException(401, 'Unauthorized')
        user = db.get(StaffUser, item.user_id)
        if not user or not user.active or user.locked_until > now:
            raise HTTPException(401, 'Unauthorized')
        return user, item

    def require_roles(*accepted):
        def verify(identity=Depends(staff_session)):
            user, item = identity
            if user.role not in accepted:
                raise HTTPException(403, 'Insufficient role')
            return user, item
        return verify

    @router.post('/login')
    def login(data: LoginIn, db: Session = Depends(session)):
        crypto = require_staging()
        now = int(time.time())
        user = db.scalar(select(StaffUser).where(
            StaffUser.username == data.username).with_for_update())
        # Always generic errors; no private username or MFA secret leaks.
        if not user or not user.active or user.locked_until > now:
            raise HTTPException(401, 'Invalid credentials or MFA')
        try:
            correct_password = PASSWORD_HASHER.verify(user.password_hash, data.password)
        except (argon_errors.VerifyMismatchError, argon_errors.VerificationError,
                argon_errors.InvalidHashError):
            correct_password = False
        seed = None
        if correct_password:
            try:
                seed = crypto.decrypt(user.mfa_ciphertext.encode()).decode('ascii')
            except (InvalidToken, UnicodeDecodeError):
                raise HTTPException(503, 'MFA encryption configuration invalid') from None
        counter = matching_counter(seed, data.otp, after=user.last_totp_counter) if seed else None
        if counter is None:
            user.failed_attempts = int(user.failed_attempts or 0) + 1
            if user.failed_attempts >= MAX_FAILURES:
                user.locked_until = now + LOCK_SECONDS
                user.failed_attempts = 0
            db.commit()
            raise HTTPException(401, 'Invalid credentials or MFA')
        user.failed_attempts = 0
        user.locked_until = 0
        user.last_totp_counter = counter
        token = secrets.token_urlsafe(32)
        item = StaffSession(user_id=user.id,
                            token_hash=hashlib.sha256(token.encode()).hexdigest(),
                            created_at=now, expires_at=now + SESSION_SECONDS, revoked=False)
        db.add(item)
        db.commit()
        return {'access_token': token, 'token_type': 'bearer',
                'expires_in': SESSION_SECONDS, 'role': user.role}

    @router.get('/me')
    def me(identity=Depends(staff_session)):
        user, item = identity
        return {'username': user.username, 'role': user.role,
                'expires_at': item.expires_at}

    @router.post('/logout')
    def logout(identity=Depends(staff_session), db: Session = Depends(session)):
        _, item = identity
        item.revoked = True
        db.commit()
        return {'revoked': True}

    @router.get('/readiness')
    def readiness(identity=Depends(require_roles('owner', 'auditor')),
                  db: Session = Depends(session)):
        return audit_provider(db)

    @router.get('/catalog-summary')
    def catalog_summary(identity=Depends(require_roles('owner', 'catalog')),
                        db: Session = Depends(session)):
        # No customer information or price-modifying operations.
        from sqlalchemy import func
        from .main import Product, Variant
        return {
            'products': int(db.scalar(select(func.count()).select_from(Product).where(
                Product.store_id == 1)) or 0),
            'available_skus': int(db.scalar(select(func.count()).select_from(Variant).where(
                Variant.store_id == 1, Variant.stock > 0, Variant.active.is_(True))) or 0)
        }

    @router.get('/order-counts')
    def order_counts(identity=Depends(require_roles('owner', 'operations')),
                     db: Session = Depends(session)):
        from sqlalchemy import func
        from .main import Order
        return {
            'orders': int(db.scalar(select(func.count()).select_from(Order).where(
                Order.store_id == 1)) or 0),
            'unpaid': int(db.scalar(select(func.count()).select_from(Order).where(
                Order.store_id == 1, Order.payment_status == 'unpaid')) or 0)
        }

    @router.get('/users')
    def users(identity=Depends(require_roles('owner')), db: Session = Depends(session)):
        return [{'id': u.id, 'username': u.username, 'role': u.role, 'active': u.active}
                for u in db.scalars(select(StaffUser).order_by(StaffUser.id)).all()]

    @router.post('/users', status_code=201)
    def add_staff(data: CreateUserIn,
                  identity=Depends(require_roles('owner')),
                  db: Session = Depends(session)):
        if data.role not in ROLES:
            raise HTTPException(422, 'Invalid role')
        try:
            user, enrollment = create_user(db, data.username, data.password, data.role)
        except ValueError:
            raise HTTPException(409, 'Invalid or existing user') from None
        # The MFA provisioning URL is returned ONCE. Never include it in
        # any other endpoint or logs. Only use via secure staging HTTPS.
        return {'username': user.username, 'role': user.role,
                'mfa_enrollment_uri': enrollment}

    @router.post('/users/{user_id}/disable')
    def disable_staff(user_id: int, identity=Depends(require_roles('owner')),
                      db: Session = Depends(session)):
        owner, _ = identity
        if owner.id == user_id:
            raise HTTPException(409, 'Cannot disable current owner')
        user = db.get(StaffUser, user_id)
        if not user:
            raise HTTPException(404, 'User not found')
        user.active = False
        sessions = db.scalars(select(StaffSession).where(
            StaffSession.user_id == user.id, StaffSession.revoked.is_(False))).all()
        for item in sessions:
            item.revoked = True
        db.commit()
        return {'disabled': True, 'sessions_revoked': len(sessions)}

    app.include_router(router)
    return StaffUser, StaffSession, create_user
