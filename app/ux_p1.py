"""BeerShop P1: privacy-minimal, synthetic-browser-only conversion counters.

No cookies, IDs, fingerprinting, IP capture, database writes, PII, network
providers, persistent storage or billing. Counters reset on process restart.
Not suitable as a production conversion attribution solution.
"""
import os
import re
import tempfile
import threading
from collections import Counter
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ACK = 'LOCAL_SYNTHETIC_BROWSER_ONLY'
EVENTS = frozenset((
    'catalog_view', 'product_view', 'cart_add',
    'checkout_open', 'checkout_attempt', 'checkout_success',
    'checkout_error', 'checkout_price_changed',
))
_LOCK = threading.Lock()
_COUNTS = Counter()


def enabled_for_fixture(database_url, persistent_url):
    if (os.getenv('BEERSHOP_P1_UX_ANALYTICS') != '1'
            or os.getenv('BEERSHOP_P1_UX_ACK') != ACK
            or persistent_url or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL')):
        return False
    try:
        url = make_url(database_url)
        path = Path(url.database or '')
        # Exact synthetic browser test directory, created under OS temp.
        temp = Path(tempfile.gettempdir()).resolve()
        return (url.get_backend_name() == 'sqlite' and not url.host
                and path.is_absolute() and path.name == 'test.db'
                and path.parent.name.startswith('beershop-browser-')
                and path.parent.resolve().is_relative_to(temp)
                and bool(re.fullmatch(r'beershop-browser-[A-Za-z0-9_-]+',
                                      path.parent.name)))
    except (ArgumentError, ValueError, TypeError, OSError):
        return False


def require_fixture(database_url, persistent_url):
    if not enabled_for_fixture(database_url, persistent_url):
        raise HTTPException(404, 'Not found')


def record(event):
    if event not in EVENTS:
        raise HTTPException(422, 'Unknown event')
    with _LOCK:
        _COUNTS[event] += 1


def report():
    with _LOCK:
        counts = {event: int(_COUNTS[event]) for event in sorted(EVENTS)}
    attempts = counts['checkout_attempt']
    return {
        'mode': 'SYNTHETIC_AGGREGATE_IN_MEMORY',
        'events': counts,
        'attempt_success_ratio': round(counts['checkout_success'] / attempts, 4)
            if attempts else None,
        'attempts': attempts,
        'storage': 'memory_only',
        'unique_visitors': None,
        'outbound_notifications': 0,
        'pii_captured': False,
        'note': ('Event counts, not unique-user conversion. Resets on restart. '
                 'No real visitor analytics or attribution is enabled.'),
    }


def preview_notification_templates():
    """Dry-run copy: not queued, sent, or linked to a real order."""
    return {
        'mode': 'TEMPLATES_ONLY_NO_DELIVERY',
        'channels_connected': [],
        'messages_sent': 0,
        'templates': [
            {'key': 'order_received', 'channel': 'email_preview',
             'subject': 'BeerShop: замовлення отримано',
             'body': ('Замовлення №TEST-0001 отримано. '
                      'Менеджер перевірить наявність і остаточну вартість. '
                      'Оплату ще не підтверджено.')},
            {'key': 'manual_payment_pending', 'channel': 'onsite_preview',
             'subject': 'Очікується підтвердження менеджера',
             'body': ('Не переказуйте кошти до узгодження реквізитів '
                      'і підсумкової суми з менеджером.')},
            {'key': 'dispatch_update', 'channel': 'sms_preview',
             'subject': 'Статус доставки',
             'body': ('TEST-0001: оновлення доставки повідомимо лише '
                      'після перевірки накладної менеджером.')},
        ],
    }
