"""v9.1 only own BeerShop CRM in strict staging; no external CRM dispatch."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import app.main as main


def test_first_party_crm_feature_switch_and_asset(monkeypatch):
    monkeypatch.delenv('BEERSHOP_V91_ENFORCE_RBAC', raising=False)
    with TestClient(main.app) as c:
        normal = c.get('/api/v91/features')
        assert normal.status_code == 200
        assert normal.json() == {'own_crm_only': False, 'crm_name': 'BeerShop CRM', 'staff_crm_login': False}
        page = c.get('/manage')
        assert page.status_code == 200
        for elem in ('opsOwnCrmV91', 'conn41OwnCrmV91',
                     'opsExternalCrmLegacy', 'conn41ExternalCrmLegacy'):
            assert 'id="'+elem+'"' in page.text
        assert '/assets/own-crm-v91.js' in page.text
        script = c.get('/assets/own-crm-v91.js')
        assert script.status_code == 200
        assert 'javascript' in script.headers['content-type']
        assert script.headers['cache-control'] == 'no-store'
        assert 'https://openapi.keycrm.app' not in script.text
        assert 'localStorage' not in script.text
    monkeypatch.setenv('BEERSHOP_V91_ENFORCE_RBAC', '1')
    monkeypatch.delenv('BEERSHOP_V9_STAFF_AUTH', raising=False)
    monkeypatch.delenv('BEERSHOP_V91_STAGING_ACK', raising=False)
    # Strict RBAC without MFA and isolated-staging ACK is not merely a
    # disabled UI mode: the app refuses startup before it can touch the DB.
    with pytest.raises(HTTPException) as blocked:
        with TestClient(main.app):
            pass
    assert blocked.value.status_code == 503
    monkeypatch.setenv('BEERSHOP_V9_STAFF_AUTH', '1')
    monkeypatch.setenv('BEERSHOP_V91_STAGING_ACK', 'ISOLATED_STAGING_ONLY')
    monkeypatch.setattr(main, 'PERSISTENT_URL', '')
    with TestClient(main.app) as c:
        assert c.get('/api/v91/features').json()['staff_crm_login'] is True


def test_disabled_keycrm_routes_covered_by_role_enforcement():
    from app.rbac_v91 import DISABLED_KEYCRM_ROUTES, ROUTE_ROLES
    expected = {
        ('GET', '/api/admin/ops/orders/{order_id}/keycrm-payload'),
        ('POST', '/api/admin/ops/orders/{order_id}/keycrm-confirm'),
        ('GET', '/api/admin/connectors41/keycrm/sources'),
        ('POST', '/api/admin/connectors41/keycrm/refresh/{order_id}'),
        ('GET', '/api/admin/connectors41/keycrm/dispatch/{order_id}'),
        ('POST', '/api/admin/connectors41/keycrm/sync/{order_id}'),
    }
    assert DISABLED_KEYCRM_ROUTES == expected
    assert expected <= set(ROUTE_ROLES)
