"""BeerShop P1 isolated aggregate analytics, notification and SEO regression."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

import app.main as main
from app import ux_p1
from app.seo_p1 import product_preview_html

ADMIN={'Authorization':'Bearer '+'t'*40}
AUDITOR={'Authorization':'Bearer '+'a'*40}


def test_p1_disabled_on_existing_ci_and_remote_databases(monkeypatch,tmp_path):
    monkeypatch.delenv('BEERSHOP_P1_UX_ANALYTICS', raising=False)
    monkeypatch.delenv('BEERSHOP_P1_UX_ACK', raising=False)
    assert not ux_p1.enabled_for_fixture(main.DB_URL, main.PERSISTENT_URL)
    monkeypatch.setenv('BEERSHOP_P1_UX_ANALYTICS','1')
    monkeypatch.setenv('BEERSHOP_P1_UX_ACK', ux_p1.ACK)
    for url in (
        'postgresql://fake:fake@remote.neon.tech/live',
        'postgresql://fake:fake@localhost/test',
        'sqlite:///./beershop_ci_test.db',
        'sqlite:////tmp/customer-db.db',
        'sqlite:////tmp/beershop-browser-not-fixture/orders.db',
    ):
        assert not ux_p1.enabled_for_fixture(url,'')
    assert not ux_p1.enabled_for_fixture(
        'sqlite:////tmp/beershop-browser-test/test.db',
        'postgresql://fake:fake@remote.neon.tech/live')
    # Named disposable browser directory is allowed only with both ACKs.
    folder = tmp_path / 'beershop-browser-local'
    folder.mkdir()
    local = 'sqlite:///' + str(folder / 'test.db')
    assert ux_p1.enabled_for_fixture(local,'') is True
    monkeypatch.delenv('BEERSHOP_P1_UX_ACK')
    assert not ux_p1.enabled_for_fixture(local,'')


def test_p1_aggregates_only_finite_events_and_never_stores_pii():
    with ux_p1._LOCK:
        ux_p1._COUNTS.clear()
    try:
        ux_p1.record('checkout_attempt')
        ux_p1.record('checkout_success')
        ux_p1.record('cart_add')
        report = ux_p1.report()
        assert report['mode'] == 'SYNTHETIC_AGGREGATE_IN_MEMORY'
        assert report['attempt_success_ratio'] == 1.0
        assert report['unique_visitors'] is None
        assert report['pii_captured'] is False
        assert report['outbound_notifications'] == 0
        assert set(report['events']) == ux_p1.EVENTS
        assert report['events']['checkout_attempt'] == 1
        assert report['events']['checkout_success'] == 1
        with pytest.raises(Exception):
            ux_p1.record('some.user@example.test')
    finally:
        with ux_p1._LOCK:
            ux_p1._COUNTS.clear()


def test_p1_preview_notifications_are_dry_run_and_no_recipient_data():
    result = ux_p1.preview_notification_templates()
    assert result['mode'] == 'TEMPLATES_ONLY_NO_DELIVERY'
    assert result['channels_connected'] == []
    assert result['messages_sent'] == 0
    assert {t['channel'] for t in result['templates']} == {
        'email_preview','sms_preview','onsite_preview'}
    assert all('TEST-0001' in t['body'] or
               'переказуйте' in t['body']
               for t in result['templates'])
    assert 'smtp' not in str(result).lower()
    assert 'sendgrid' not in str(result).lower()


def test_p1_seo_product_preview_escapes_untrusted_markup():
    source = (
        '<!doctype html><html><head>'
        '<title>BeerShop — обладнання для розливу пива</title>'
        '</head><body></body></html>'
    )
    product = SimpleNamespace(
        name='<img src=x onerror=alert(1)> Тест & "товар"',
        description='<script>evil()</script> Чистий тест "опис" & бренд')
    rendered = product_preview_html(source, product)
    assert '<meta name="robots" content="noindex,nofollow">' in rendered
    assert '<meta property="og:type" content="product">' in rendered
    assert 'onerror=' not in rendered
    assert '<script>' not in rendered
    assert 'Тест &amp; &quot;товар&quot;' in rendered
    assert '&quot;опис&quot; &amp; бренд' in rendered
    assert '<link rel="canonical"' not in rendered
    with pytest.raises(ValueError):
        product_preview_html('<head></head>',product)


def test_p1_existing_app_disallows_reporting_without_fixture(monkeypatch):
    monkeypatch.delenv('BEERSHOP_P1_UX_ANALYTICS', raising=False)
    monkeypatch.delenv('BEERSHOP_P1_UX_ACK', raising=False)
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN','t'*40)
    monkeypatch.setenv('BEERSHOP_V9_AUDIT_TOKEN','a'*40)
    with TestClient(main.app) as c:
        capabilities = c.get('/api/p1/ux-capabilities')
        assert capabilities.status_code == 200
        assert capabilities.json()['analytics_enabled'] is False
        assert c.post('/api/p1/ux-event',json={'event':'checkout_success'}).status_code==404
        assert c.post('/api/p1/ux-event',json={
            'event':'checkout_success', 'email':'fake@example.test'}).status_code==422
        for route in ('/api/admin/p1/ux-report',
                      '/api/admin/p1/notification-previews'):
            assert c.get(route).status_code == 401
            assert c.get(route,headers=ADMIN).status_code == 404
            assert c.get(route,headers=AUDITOR).status_code == 404
        assert c.get('/assets/ux-p1.js').status_code==200
        assert c.get('/assets/ux-p1.css').status_code==200
