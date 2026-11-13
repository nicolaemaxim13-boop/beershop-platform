"""v9.5 opt-in rate limit with synthetic request bodies, local CI only."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import text

from app import main
from app import api_rate_v95 as rate_v95
from app.api_rate_v95 import classify, validate_staging, normalized_client


def env_v95(monkeypatch):
    monkeypatch.setenv("BEERSHOP_V95_RATE_LIMIT", "1")
    monkeypatch.setenv("BEERSHOP_V91_STAGING_ACK", "ISOLATED_STAGING_ONLY")
    monkeypatch.setenv("BEERSHOP_V95_RATE_KEY", "synthetic-only-" + uuid4().hex * 2)
    monkeypatch.setenv("BEERSHOP_ADMIN_TOKEN", "t"*40)
    # Fixed clock avoids a quota assertion crossing a wall-clock minute.
    monkeypatch.setattr(rate_v95, "rate_limit_clock", lambda: 1_900_000_020)


def test_v95_staging_rejects_remote_without_any_migration(monkeypatch):
    env_v95(monkeypatch)
    with pytest.raises(HTTPException) as err:
        validate_staging(
            database_url="postgresql://readonly:fake@ep-neon.neon.tech/live",
            persistent_url="")
    assert err.value.status_code == 503
    with pytest.raises(HTTPException):
        validate_staging(database_url="sqlite:///:memory:", persistent_url="anything")
    monkeypatch.delenv("BEERSHOP_V91_STAGING_ACK")
    with pytest.raises(HTTPException):
        validate_staging(database_url="sqlite:///./isolated.db", persistent_url="")


def test_v95_routes_are_exact_and_not_header_dependent():
    assert classify("POST", "/api/orders") == "checkout"
    assert classify("POST", "/api/admin/v9/staff/login") == "staff_login"
    assert classify("GET", "/api/admin/orders") == "admin_read"
    assert classify("PATCH", "/api/admin/orders/12/status") == "admin_write"
    assert classify("GET", "/api/products") is None
    assert classify("POST", "/api/not-real") is None


def test_v95_rate_limit_per_zone_and_no_raw_peer(monkeypatch):
    env_v95(monkeypatch)
    with TestClient(main.app) as client:
        # Invalid JSON is enough to exercise middleware but cannot create orders.
        for n in range(15):
            r = client.post("/api/orders", json={})
            assert r.status_code == 422, (n, r.text)
        denied = client.post("/api/orders", json={})
        assert denied.status_code == 429, denied.text
        assert 1 <= int(denied.headers["retry-after"]) <= 60
        assert denied.json() == {"detail":"Request rate exceeded"}
        # Other API zones have independent limits.
        assert client.get("/api/products").status_code != 429
        # The peer hash is HMAC, not a raw forwarded header or browser identifier.
        with main.SessionLocal() as db:
            stored = db.execute(text("SELECT key_hash, hits FROM v95_api_rate_counters")).all()
            assert any(hits >= 16 for _, hits in stored)
            assert all(len(h) == 64 for h, _ in stored)
            assert "testclient" not in str(stored)
            assert "10.10.10.10" not in str(stored)


def test_v95_login_and_admin_limits_are_separate(monkeypatch):
    env_v95(monkeypatch)
    with TestClient(main.app) as client:
        headers = {"X-Forwarded-For": "10.10.10.10", "X-Real-IP": "192.0.2.5"}
        for _ in range(12):
            res = client.post("/api/admin/v9/staff/login", json={}, headers=headers)
            assert res.status_code == 422
        res = client.post("/api/admin/v9/staff/login", json={}, headers={
            "X-Forwarded-For": "8.8.8.8"})
        assert res.status_code == 429  # Forwarded header cannot bypass limit
        for _ in range(20):
            res = client.patch("/api/admin/orders/0/status", json={})
            assert res.status_code in (401, 503)
        assert client.patch("/api/admin/orders/0/status", json={}).status_code == 429
        assert client.get("/api/admin/orders").status_code in (401,503)


def test_v95_disabled_does_not_throttle(monkeypatch):
    monkeypatch.delenv("BEERSHOP_V95_RATE_LIMIT", raising=False)
    with TestClient(main.app) as client:
        assert all(client.post("/api/orders", json={}).status_code == 422
                   for _ in range(18))
