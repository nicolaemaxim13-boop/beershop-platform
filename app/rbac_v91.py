"""BeerShop v9.1: isolated-staging, fail-closed legacy-admin RBAC bridge.

No runtime route discovery or default permission: each method+route template is
explicitly mapped. Staff bearer tokens do NOT pass old shared-admin auth when
strict mode is enabled. This is an opt-in migration pilot, NOT production ready.
"""
import os
from fastapi import HTTPException

OWNER = frozenset({'owner'})
CATALOG = frozenset({'owner', 'catalog'})
OPERATIONS = frozenset({'owner', 'operations'})
FINANCE = frozenset({'owner', 'finance'})
ORDER_READ = frozenset({'owner', 'operations', 'finance'})
INVENTORY_READ = frozenset({'owner', 'catalog', 'operations'})
READINESS = frozenset({'owner', 'auditor'})

# Server-side allowlist. Never authorize from button visibility, prefix matches
# or frontend role labels. Routes not present here receive 403.
ROUTE_ROLES = {
    ('GET', '/api/admin/pricing/policy'): FINANCE,
    ('POST', '/api/admin/pricing/preview'): FINANCE,
    ('PUT', '/api/admin/pricing/policy'): FINANCE,
    ('POST', '/api/admin/categories'): CATALOG,
    ('POST', '/api/admin/products'): CATALOG,
    ('POST', '/api/admin/products/{product_id}/variants'): OWNER,  # supplier cost
    ('PATCH', '/api/admin/variants/{variant_id}'): OWNER,  # can edit price
    ('GET', '/api/admin/products'): CATALOG,
    ('PATCH', '/api/admin/products/{product_id}'): CATALOG,
    ('PUT', '/api/admin/products/{product_id}/gallery'): CATALOG,
    ('POST', '/api/admin/products/{product_id}/photo'): CATALOG,
    ('POST', '/api/admin/import/preview'): CATALOG,
    ('GET', '/api/admin/import/history'): CATALOG,
    ('POST', '/api/admin/prom/preview'): CATALOG,
    ('POST', '/api/admin/prom/apply'): OWNER,  # may overwrite supplier prices
    ('GET', '/api/admin/bulk/catalog'): CATALOG,
    ('POST', '/api/admin/bulk/preview'): CATALOG,
    ('POST', '/api/admin/bulk/apply'): OWNER,  # may change many SKU prices
    ('GET', '/api/admin/catalog32'): CATALOG,
    ('GET', '/api/admin/catalog32/{product_id}'): CATALOG,
    ('GET', '/api/admin/categories32'): CATALOG,
    ('PATCH', '/api/admin/categories32/{category_id}'): CATALOG,
    ('GET', '/api/admin/orders'): ORDER_READ,
    ('PATCH', '/api/admin/orders/{order_id}/status'): OPERATIONS,
    ('GET', '/api/admin/orders/{order_id}/invoice'): FINANCE,
    ('PATCH', '/api/admin/orders/{order_id}/payment'): FINANCE,
    ('GET', '/api/admin/ops/orders'): ORDER_READ,
    ('GET', '/api/admin/ops/summary'): ORDER_READ,
    ('POST', '/api/admin/ops/orders/{order_id}/bank-confirm'): FINANCE,
    ('POST', '/api/admin/ops/orders/{order_id}/nova-poshta/waybill'): OPERATIONS,
    ('PATCH', '/api/admin/ops/orders/{order_id}/nova-poshta/status'): OPERATIONS,
    ('GET', '/api/admin/ops/orders/{order_id}/keycrm-payload'): OPERATIONS,
    ('POST', '/api/admin/ops/orders/{order_id}/keycrm-confirm'): OPERATIONS,
    ('GET', '/api/admin/ops/orders/{order_id}/audit'): OPERATIONS,
    ('GET', '/api/admin/inventory/summary'): INVENTORY_READ,
    ('GET', '/api/admin/inventory'): INVENTORY_READ,
    ('GET', '/api/admin/inventory/{variant_id}'): INVENTORY_READ,
    ('PATCH', '/api/admin/inventory/{variant_id}'): CATALOG,
    ('GET', '/api/admin/crm/summary'): OPERATIONS,
    ('POST', '/api/admin/crm/index-orders'): OPERATIONS,
    ('GET', '/api/admin/crm/orders'): OPERATIONS,
    ('GET', '/api/admin/crm/orders/{order_id}'): OPERATIONS,
    ('PATCH', '/api/admin/crm/orders/{order_id}'): OPERATIONS,
    ('POST', '/api/admin/crm/orders/{order_id}/notes'): OPERATIONS,
    ('GET', '/api/admin/crm/customers'): OPERATIONS,
    ('GET', '/api/admin/crm/customers/{customer_id}'): OPERATIONS,
    ('POST', '/api/admin/crm/customers'): OPERATIONS,
    ('PATCH', '/api/admin/crm/customers/{customer_id}'): OPERATIONS,
    ('POST', '/api/admin/crm/customers/{customer_id}/notes'): OPERATIONS,
    ('GET', '/api/admin/crm/managers'): OPERATIONS,
    ('POST', '/api/admin/crm/managers'): OPERATIONS,
    ('PATCH', '/api/admin/crm/managers/{manager_id}'): OPERATIONS,
    ('GET', '/api/admin/connectors41/status'): OWNER,
    ('GET', '/api/admin/connectors41/keycrm/sources'): OPERATIONS,
    ('GET', '/api/admin/connectors41/privatbank/availability'): FINANCE,
    ('GET', '/api/admin/connectors41/snapshot/{order_id}'): ORDER_READ,
    ('POST', '/api/admin/connectors41/keycrm/refresh/{order_id}'): OPERATIONS,
    ('GET', '/api/admin/connectors41/keycrm/dispatch/{order_id}'): OPERATIONS,
    ('POST', '/api/admin/connectors41/keycrm/sync/{order_id}'): OPERATIONS,
    ('GET', '/api/admin/connectors41/nova-poshta/tracking/{order_id}'): OPERATIONS,
    ('POST', '/api/admin/connectors41/nova-poshta/sync/{order_id}'): OPERATIONS,
    ('GET', '/api/admin/maintenance/report'): READINESS,
    ('GET', '/api/admin/maintenance/export'): OWNER,
}


# Our own BeerShop CRM is the only intended order/customer system.
# KeyCRM remains as legacy code for compatibility, but ALL its routes
# (including manual external-ID confirmation and JSON export) are disabled
# in v9.1 strict staging. No customer PII can leave for external CRM.
DISABLED_KEYCRM_ROUTES = frozenset({
    ('GET', '/api/admin/ops/orders/{order_id}/keycrm-payload'),
    ('POST', '/api/admin/ops/orders/{order_id}/keycrm-confirm'),
    ('GET', '/api/admin/connectors41/keycrm/sources'),
    ('POST', '/api/admin/connectors41/keycrm/refresh/{order_id}'),
    ('GET', '/api/admin/connectors41/keycrm/dispatch/{order_id}'),
    ('POST', '/api/admin/connectors41/keycrm/sync/{order_id}'),
})


# In the strict staging rehearsal, connector endpoints which can contact real
# third parties are disabled even for owners. Explicit sync tests use synthetic
# mocks on a separate fixture, never external provider credentials.
EXTERNAL_NETWORK_ROUTES = frozenset({
    ('GET', '/api/admin/connectors41/keycrm/sources'),
    ('POST', '/api/admin/connectors41/keycrm/refresh/{order_id}'),
    ('POST', '/api/admin/connectors41/keycrm/sync/{order_id}'),
    ('GET', '/api/admin/connectors41/nova-poshta/tracking/{order_id}'),
    ('POST', '/api/admin/connectors41/nova-poshta/sync/{order_id}'),
})



def strict_rbac_enabled():
    return os.getenv('BEERSHOP_V91_ENFORCE_RBAC') == '1'


def require_isolated_staging(*, persistent_url=''):
    # Any incomplete configuration is a service error, never a legacy bypass.
    if (os.getenv('BEERSHOP_V91_STAGING_ACK') != 'ISOLATED_STAGING_ONLY'
            or os.getenv('BEERSHOP_V9_STAFF_AUTH') != '1'
            or persistent_url):
        raise HTTPException(503, 'v9.1 RBAC requires an isolated staging environment')


def allowed_roles_for(method, route_path):
    return ROUTE_ROLES.get((method.upper(), route_path))


def authorize_legacy_admin_request(*, request, authorization, db, user_model,
                                   session_model, persistent_url=''):
    """Use the existing MFA-backed session identity; reject unmapped routes."""
    require_isolated_staging(persistent_url=persistent_url)
    from .staff_v9 import verify_bearer_staff
    user, item = verify_bearer_staff(authorization, db, user_model, session_model)
    route = request.scope.get('route')
    template = getattr(route, 'path', None)
    allowed = allowed_roles_for(request.method, template)
    if not allowed or user.role not in allowed:
        raise HTTPException(403, 'Insufficient role or unregistered admin route')
    if (request.method, template) in DISABLED_KEYCRM_ROUTES:
        raise HTTPException(503, 'KeyCRM not used: BeerShop CRM is authoritative')
    if (request.method, template) in EXTERNAL_NETWORK_ROUTES:
        raise HTTPException(503, 'External provider requests disabled in staging')
    return user
