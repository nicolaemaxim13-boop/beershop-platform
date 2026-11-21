"""v9.6 optional signed reverse-proxy identity for staging-only rate limiting.

Only exact IP peers listed by the operator may send verified client identities.
Never use spoofable X-Forwarded-For or X-Real-IP to choose a quota bucket.
A trusted proxy MUST strip incoming X-BeerShop-Edge-* headers before signing.
"""
import hashlib
import hmac
import ipaddress
import os
import re
import time

from fastapi import HTTPException


EDGE_IP = 'X-BeerShop-Edge-IP'
EDGE_TIME = 'X-BeerShop-Edge-Time'
EDGE_SIGNATURE = 'X-BeerShop-Edge-Signature'


def enabled():
    return os.getenv('BEERSHOP_V96_EDGE_SIGNED_CLIENT') == '1'


def validate_edge_config():
    if not enabled():
        return
    if os.getenv('BEERSHOP_V95_RATE_LIMIT') != '1':
        raise HTTPException(503, 'Signed edge identity requires staging rate limits')
    key = os.getenv('BEERSHOP_V96_EDGE_KEY', '')
    if len(key.encode('utf-8')) < 32:
        raise HTTPException(503, 'Signed edge verification key not configured')
    raw = os.getenv('BEERSHOP_V96_TRUSTED_EDGE_PEERS', '')
    peers = raw.split(',')
    if not 1 <= len(peers) <= 8:
        raise HTTPException(503, 'Expected up to eight explicit trusted proxy IP addresses')
    try:
        if not all(p and p.strip() == p and str(ipaddress.ip_address(p)) == p for p in peers):
            raise ValueError('Non-canonical peer')
    except ValueError:
        raise HTTPException(503, 'Invalid trusted proxy IP configuration') from None
    if len(set(peers)) != len(peers):
        raise HTTPException(503, 'Duplicate proxy IP configuration')


def sign_edge_identity(ip, timestamp, key):
    """Called only by private CI fixtures; a real proxy signs outside this app."""
    message = ('beershop-v96:' + ip + ':' + str(timestamp)).encode('ascii')
    return hmac.new(key.encode('utf-8'), message, hashlib.sha256).hexdigest()


def verified_edge_client(request, *, now=None):
    if not enabled():
        from .api_rate_v95 import normalized_client
        return normalized_client(request)
    validate_edge_config()
    peer = getattr(request.client, 'host', '') if request.client else ''
    if peer not in set(os.environ['BEERSHOP_V96_TRUSTED_EDGE_PEERS'].split(',')):
        raise HTTPException(403, 'Untrusted reverse proxy peer')
    # Duplicate headers are always rejected: framework .get() alone can hide
    # ambiguity between edge and downstream interpretations.
    fields = {}
    for name in (EDGE_IP, EDGE_TIME, EDGE_SIGNATURE):
        entries = request.headers.getlist(name)
        if len(entries) != 1:
            raise HTTPException(403, 'Verified proxy identity required')
        fields[name] = entries[0]
    raw_ip, ts, signature = (
        fields[EDGE_IP], fields[EDGE_TIME], fields[EDGE_SIGNATURE])
    try:
        ip = str(ipaddress.ip_address(raw_ip))
    except (ValueError, TypeError):
        raise HTTPException(403, 'Invalid signed client identity') from None
    if ip != raw_ip or not re.fullmatch(r'[0-9]{10,11}', ts):
        raise HTTPException(403, 'Invalid signed client identity')
    wall = int(time.time()) if now is None else int(now)
    if abs(wall - int(ts)) > 30 or not re.fullmatch(r'[0-9a-f]{64}', signature):
        raise HTTPException(403, 'Stale or invalid proxy signature')
    correct = sign_edge_identity(raw_ip, ts, os.environ['BEERSHOP_V96_EDGE_KEY'])
    if not hmac.compare_digest(correct, signature):
        raise HTTPException(403, 'Invalid proxy signature')
    return ip
