"""Opt-in, database-atomic API throttle for isolated BeerShop v9.5 staging.

Scope: staff login, administrative API and public checkout. Client address is
read ONLY from ASGI request.client, never untrusted forwarded headers.
This does not replace a reverse-proxy/WAF rate limiter.
"""
import hashlib
import hmac
import ipaddress
import os
import time

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column


LIMITS = {
    "staff_login": 12,
    "admin_write": 20,
    "admin_read": 90,
    "checkout": 15,
}
WINDOW_SECONDS = 60


def enabled():
    return os.getenv("BEERSHOP_V95_RATE_LIMIT") == "1"


def validate_staging(*, database_url, persistent_url):
    """Refuse to activate on production or unknown database configurations."""
    if not enabled():
        return
    from .rbac_v91 import verify_staging_database
    if os.getenv("BEERSHOP_V91_STAGING_ACK") != "ISOLATED_STAGING_ONLY":
        raise HTTPException(503, "v9.5 rate limits require isolated staging ACK")
    verify_staging_database(database_url=database_url, persistent_url=persistent_url)
    key = os.getenv("BEERSHOP_V95_RATE_KEY", "")
    if len(key.encode("utf-8")) < 32:
        raise HTTPException(503, "v9.5 rate key not configured")
    from .trusted_proxy_v96 import validate_edge_config
    validate_edge_config()


def classify(method, path):
    if method == "POST" and path == "/api/admin/v9/staff/login":
        return "staff_login"
    if path == "/api/orders" and method == "POST":
        return "checkout"
    if path.startswith("/api/admin/"):
        return "admin_read" if method in ("GET", "HEAD") else "admin_write"
    return None


def normalized_client(request):
    # ASGI peer, not X-Forwarded-For / X-Real-IP (spoofable without trusted proxy).
    raw = getattr(request.client, "host", "") if request.client else ""
    try:
        return str(ipaddress.ip_address(raw))
    except (ValueError, TypeError):
        # Non-IP clients share a single conservative pool.
        return "unknown-peer"


def install_api_limiter(app, Base, SessionLocal, *, database_url, persistent_url):
    class ApiRateCounter(Base):
        __tablename__ = "v95_api_rate_counters"
        key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
        window_start: Mapped[int] = mapped_column(Integer, nullable=False)
        hits: Mapped[int] = mapped_column(Integer, nullable=False)

    @app.middleware("http")
    async def rate_limit_middleware(request, call_next):
        if not enabled():
            return await call_next(request)
        validate_staging(database_url=database_url, persistent_url=persistent_url)
        zone = classify(request.method, request.url.path)
        if not zone:
            return await call_next(request)
        now = int(time.time())
        start = now - now % WINDOW_SECONDS
        from .trusted_proxy_v96 import verified_edge_client
        try:
            client_address = verified_edge_client(request)
        except HTTPException:
            return JSONResponse(
                status_code=403,
                content={"detail": "Verified proxy identity required"},
                headers={"Cache-Control": "no-store"})
        raw = "v95:" + zone + ":" + client_address
        key = os.environ["BEERSHOP_V95_RATE_KEY"].encode()
        digest = hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()
        with SessionLocal.begin() as session:
            # Atomic cross-process increment. This is DB-backed, not an in-memory
            # per-worker counter. SQLite and PostgreSQL use compatible upsert.
            session.execute(text("""
                INSERT INTO v95_api_rate_counters (key_hash, window_start, hits)
                VALUES (:digest, :start, 1)
                ON CONFLICT (key_hash) DO UPDATE SET
                    hits = CASE
                        WHEN v95_api_rate_counters.window_start = :start
                        THEN v95_api_rate_counters.hits + 1 ELSE 1 END,
                    window_start = :start
            """), {"digest": digest, "start": start})
            hits = session.execute(text("""
                SELECT hits FROM v95_api_rate_counters WHERE key_hash=:digest
            """), {"digest": digest}).scalar_one()
        if hits > LIMITS[zone]:
            retry = max(1, WINDOW_SECONDS - (now - start))
            return JSONResponse(
                status_code=429, content={"detail": "Request rate exceeded"},
                headers={"Retry-After": str(retry), "Cache-Control": "no-store"})
        return await call_next(request)

    return ApiRateCounter
