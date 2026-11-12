"""Opt-in, staging-only identity pilot for BeerShop v9.

Password: Argon2id. TOTP: RFC 6238 with single-use counters.
TOTP seeds: Fernet-encrypted with a key kept *outside* the database.
Sessions: short-lived, SHA-256 hashed random bearer tokens, revocable.

WARNING: Sessions authorize existing legacy /api/admin endpoints ONLY when
BEERSHOP_V91_ENFORCE_RBAC=1 and staging isolation is explicitly acknowledged.
All other environments retain the legacy token; do not activate on production.
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
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, select, text, delete
from sqlalchemy.orm import Mapped, mapped_column, Session

ROLES = ('owner', 'catalog', 'operations', 'finance', 'auditor')
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
SESSION_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60
MAX_FAILURES = 5
# Additional database-wide quota includes unknown usernames.
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LIMIT_FAILURES = 8
LOGIN_BLOCK_SECONDS = 15 * 60


def is_enabled():
    return os.getenv('BEERSHOP_V9_STAFF_AUTH', '') == '1'


def require_staging():
    if not is_enabled():
        raise HTTPException(503, 'Staff identity pilot disabled')
    # Check initialized engine settings, not a user-controlled request header.
    # Prevent accidental MFA activation against live Neon/PostgreSQL.
    from . import main
    from .rbac_v91 import verify_staging_database
    verify_staging_database(persistent_url=main.PERSISTENT_URL, database_url=main.DB_URL)
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


def verify_bearer_staff(authorization, db, user_model, session_model):
    """Shared session verifier for pilot routes and strict legacy-admin bridge."""
    require_staging()
    token = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    if len(token) < 32 or len(token) > 256:
        raise HTTPException(401, 'Unauthorized')
    digest = hashlib.sha256(token.encode()).hexdigest()
    item = db.scalar(select(session_model).where(session_model.token_hash == digest))
    now = int(time.time())
    if not item or item.revoked or item.expires_at <= now:
        raise HTTPException(401, 'Unauthorized')
    user = db.get(user_model, item.user_id)
    if not user or not user.active or user.locked_until > now:
        raise HTTPException(401, 'Unauthorized')
    return user, item


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

    class StaffLoginThrottle(Base):
        __tablename__ = 'v9_staff_login_throttle'
        subject_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
        window_start: Mapped[int] = mapped_column(Integer, nullable=False)
        failures: Mapped[int] = mapped_column(Integer, nullable=False)
        blocked_until: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    class StaffSecurityEvent(Base):
        __tablename__ = 'v9_staff_security_events'
        id: Mapped[int] = mapped_column(primary_key=True)
        event_type: Mapped[str] = mapped_column(String(36), nullable=False)
        subject_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
        occurred_at: Mapped[int] = mapped_column(Integer, nullable=False)
        actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def digest_subject(username):
        # No username, token, password, TOTP or IP is stored in these tables.
        secret = base64.urlsafe_b64decode(os.environ['BEERSHOP_V9_MFA_KEY'].encode())
        return hmac.new(secret, ('staff-auth:' + username.lower()).encode(),
                        hashlib.sha256).hexdigest()

    def audit(db, event_type, username, now, actor_id=None):
        db.add(StaffSecurityEvent(
            event_type=event_type, subject_hash=digest_subject(username),
            occurred_at=now, actor_user_id=actor_id))

    def record_failed_login(db, username, now, user=None):
        """Atomic cross-process per-username throttling on SQLite/PostgreSQL."""
        if user is not None:
            user.failed_attempts = int(user.failed_attempts or 0) + 1
            if user.failed_attempts >= MAX_FAILURES:
                user.locked_until = now + LOCK_SECONDS
                user.failed_attempts = 0
        db.execute(text("""
            INSERT INTO v9_staff_login_throttle
                (subject_hash, window_start, failures, blocked_until)
            VALUES (:subject, :now, 1, 0)
            ON CONFLICT (subject_hash) DO UPDATE SET
                window_start = CASE
                    WHEN v9_staff_login_throttle.window_start <= :cutoff
                    THEN :now ELSE v9_staff_login_throttle.window_start END,
                failures = CASE
                    WHEN v9_staff_login_throttle.window_start <= :cutoff
                    THEN 1 ELSE v9_staff_login_throttle.failures + 1 END,
                blocked_until = CASE
                    WHEN v9_staff_login_throttle.window_start <= :cutoff THEN 0
                    WHEN v9_staff_login_throttle.blocked_until > :now
                    THEN v9_staff_login_throttle.blocked_until
                    WHEN v9_staff_login_throttle.failures + 1 >= :limit
                    THEN :now + :duration
                    ELSE 0 END
        """), {'subject': digest_subject(username), 'now': now,
               'cutoff': now - LOGIN_WINDOW_SECONDS,
               'limit': LOGIN_LIMIT_FAILURES, 'duration': LOGIN_BLOCK_SECONDS})
        # 429 replies are not logged, to avoid flooding audit on blocked requests.
        audit(db, 'login_failed', username, now)
        db.commit()

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
        return verify_bearer_staff(authorization, db, StaffUser, StaffSession)

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
        # Use server-side DB state; never trust client-supplied forwarding headers.
        throttle = db.get(StaffLoginThrottle, digest_subject(data.username))
        if throttle and throttle.blocked_until > now:
            raise HTTPException(429, 'Too many login attempts',
                                headers={'Retry-After': str(throttle.blocked_until - now)})
        user = db.scalar(select(StaffUser).where(
            StaffUser.username == data.username).with_for_update())
        # Existing, inactive, locked and unknown usernames get generic errors.
        if not user or not user.active or user.locked_until > now:
            record_failed_login(db, data.username, now)
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
            record_failed_login(db, data.username, now, user=user)
            raise HTTPException(401, 'Invalid credentials or MFA')
        user.failed_attempts = 0
        user.locked_until = 0
        user.last_totp_counter = counter
        token = secrets.token_urlsafe(32)
        item = StaffSession(user_id=user.id,
                            token_hash=hashlib.sha256(token.encode()).hexdigest(),
                            created_at=now, expires_at=now + SESSION_SECONDS, revoked=False)
        db.add(item)
        db.execute(delete(StaffLoginThrottle).where(
            StaffLoginThrottle.subject_hash == digest_subject(data.username)))
        audit(db, 'login_success', data.username, now, actor_id=user.id)
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
        user, item = identity
        item.revoked = True
        audit(db, 'logout', user.username, int(time.time()), actor_id=user.id)
        db.commit()
        return {'revoked': True}

    @router.post('/sessions/revoke-all')
    def revoke_all_own_sessions(identity=Depends(staff_session),
                                db: Session = Depends(session)):
        user, _ = identity
        sessions = db.scalars(select(StaffSession).where(
            StaffSession.user_id == user.id, StaffSession.revoked.is_(False))).all()
        for s in sessions:
            s.revoked = True
        audit(db, 'sessions_revoked_all', user.username, int(time.time()), actor_id=user.id)
        db.commit()
        return {'revoked_sessions': len(sessions), 'all_current_sessions_closed': True}

    @router.post('/users/{user_id}/revoke-sessions')
    def owner_revoke_sessions(user_id: int, identity=Depends(require_roles('owner')),
                              db: Session = Depends(session)):
        user = db.get(StaffUser, user_id)
        if not user:
            raise HTTPException(404, 'User not found')
        sessions = db.scalars(select(StaffSession).where(
            StaffSession.user_id == user_id, StaffSession.revoked.is_(False))).all()
        for s in sessions:
            s.revoked = True
        owner, _ = identity
        audit(db, 'sessions_revoked_by_owner', user.username, int(time.time()),
              actor_id=owner.id)
        db.commit()
        return {'revoked_sessions': len(sessions)}

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
    def order_counts(identity=Depends(require_roles('owner', 'operations', 'finance')),
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
        audit(db, 'staff_created', user.username, int(time.time()), actor_id=identity[0].id)
        db.commit()
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
        audit(db, 'staff_disabled', user.username, int(time.time()), actor_id=owner.id)
        db.commit()
        return {'disabled': True, 'sessions_revoked': len(sessions)}

    @router.get('/security-events')
    def security_events(limit: int = Query(default=50, ge=1, le=100),
                        identity=Depends(require_roles('owner')),
                        db: Session = Depends(session)):
        rows = db.scalars(select(StaffSecurityEvent).order_by(
            StaffSecurityEvent.id.desc()).limit(limit)).all()
        return [{'event': item.event_type, 'at': item.occurred_at,
                 'subject_hash': item.subject_hash, 'actor_user_id': item.actor_user_id}
                for item in rows]

    app.include_router(router)
    return StaffUser, StaffSession, create_user
