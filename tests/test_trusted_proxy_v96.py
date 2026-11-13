"""v9.6 verified proxy identity: no trust in X-Forwarded-For."""
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.trusted_proxy_v96 import (
    validate_edge_config, sign_edge_identity, verified_edge_client,
)


EDGE_KEY = 'v96-opaque-synthetic-trusted-edge-signing-key-2026'


def fixture_config(monkeypatch):
    monkeypatch.setenv('BEERSHOP_V95_RATE_LIMIT', '1')
    monkeypatch.setenv('BEERSHOP_V91_STAGING_ACK', 'ISOLATED_STAGING_ONLY')
    monkeypatch.setenv('BEERSHOP_V95_RATE_KEY', 'v95-synthetic-'+uuid4().hex*2)
    monkeypatch.setenv('BEERSHOP_V96_EDGE_SIGNED_CLIENT', '1')
    monkeypatch.setenv('BEERSHOP_V96_EDGE_KEY', EDGE_KEY)
    monkeypatch.setenv('BEERSHOP_V96_TRUSTED_EDGE_PEERS', '127.0.0.1')
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't'*40)


def signed_headers(client, now=None, forwarded='198.51.100.8'):
    timestamp = str(int(time.time()) if now is None else now)
    return {
        'X-BeerShop-Edge-IP': client,
        'X-BeerShop-Edge-Time': timestamp,
        'X-BeerShop-Edge-Signature':
            sign_edge_identity(client, timestamp, EDGE_KEY),
        'X-Forwarded-For': forwarded,
        'X-Real-IP': '203.0.113.254',
    }


def request_with_headers(headers, *, peer='127.0.0.1'):
    header_list = [(k.lower().encode('ascii'), v.encode('ascii'))
                   for k,v in headers]
    return Request({
        'type':'http', 'method':'POST', 'path':'/api/orders',
        'scheme':'http', 'query_string':b'',
        'headers':header_list, 'server':('testserver', 80),
        'client':(peer, 55880)
    })


def test_v96_signed_client_validation_and_spoof_rejection(monkeypatch):
    fixture_config(monkeypatch)
    validate_edge_config()
    now = int(time.time())
    signed = signed_headers('192.0.2.14', now=now)
    assert verified_edge_client(request_with_headers(list(signed.items())), now=now) == '192.0.2.14'
    signed['X-Forwarded-For'] = '1.1.1.1'
    assert verified_edge_client(request_with_headers(list(signed.items())), now=now) == '192.0.2.14'
    for headers in (
        {'X-Forwarded-For': '192.0.2.14'},
        {**signed, 'X-BeerShop-Edge-Signature': '0'*64},
        signed_headers('192.0.2.14', now=now-120),
        {**signed, 'X-BeerShop-Edge-IP': '192.0.2.15'},
    ):
        with pytest.raises(HTTPException) as exc:
            verified_edge_client(request_with_headers(list(headers.items())), now=now)
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        verified_edge_client(request_with_headers(list(signed.items()), peer='203.0.113.1'), now=now)
    duplicate = list(signed.items()) + [('X-BeerShop-Edge-IP','192.0.2.14')]
    with pytest.raises(HTTPException):
        verified_edge_client(request_with_headers(duplicate), now=now)


def test_v96_edge_key_configuration_fail_closed(monkeypatch):
    fixture_config(monkeypatch)
    monkeypatch.setenv('BEERSHOP_V96_TRUSTED_EDGE_PEERS', '0.0.0.0/0')
    with pytest.raises(HTTPException) as exc:
        validate_edge_config()
    assert exc.value.status_code == 503
    monkeypatch.setenv('BEERSHOP_V96_TRUSTED_EDGE_PEERS', '127.0.0.1')
    monkeypatch.setenv('BEERSHOP_V96_EDGE_KEY', 'not-long-enough')
    with pytest.raises(HTTPException):
        validate_edge_config()
    monkeypatch.setenv('BEERSHOP_V96_EDGE_KEY', EDGE_KEY)
    monkeypatch.delenv('BEERSHOP_V95_RATE_LIMIT', raising=False)
    with pytest.raises(HTTPException):
        validate_edge_config()


def test_v96_signed_edge_is_used_for_independent_quota_buckets(monkeypatch):
    fixture_config(monkeypatch)
    # All requests originate at the same trusted peer. Only an edge-signed
    # client changes the bucket; a forged XFF cannot bypass the quota.
    with TestClient(main.app, client=('127.0.0.1', 50000)) as client:
        headers = signed_headers('192.0.2.2')
        assert client.post('/api/orders', json={}).status_code == 422
        for n in range(14):
            assert client.post('/api/orders', json={}, headers=headers).status_code == 422
        assert client.post('/api/orders', json={}, headers={
            **headers, 'X-Forwarded-For':'1.2.3.4'}).status_code == 422
        assert client.post('/api/orders', json={}, headers=headers).status_code == 429
        assert client.post('/api/orders', json={},
                           headers=signed_headers('192.0.2.3')).status_code == 422
        assert client.post('/api/orders', json={},
                           headers={**headers, 'X-BeerShop-Edge-Signature':'0'*64}
                           ).status_code == 403
