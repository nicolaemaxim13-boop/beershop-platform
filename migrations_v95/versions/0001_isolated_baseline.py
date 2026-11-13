"""Initial immutable fixture baseline (creates empty schema, never imports data).

Revision ID: 0001_v95
Revises:
"""
from alembic import op
from sqlalchemy import inspect

revision = "0001_v95"
down_revision = None
branch_labels = None
depends_on = None

# Frozen table inventory for this staging-only bootstrap. Column definitions
# still rely on app.Base metadata: not a production-safe historical migration.
BASE_TABLES = frozenset((
    "stores", "categories", "products", "product_images", "variants",
    "import_logs", "orders", "order_items", "fx_states", "pricing_policies",
    "order_operations", "operations_audit", "stock_reservations",
    "stock_movements", "crm_customers", "crm_managers",
    "crm_order_details", "crm_notes", "crm_dispatches",
    "v9_staff_users", "v9_staff_sessions",
    "v9_staff_login_throttle", "v9_staff_security_events",
    "v9_staff_mfa_recovery",
))


def upgrade():
    from app.main import Base
    bind = op.get_bind()
    names = set(inspect(bind).get_table_names())
    if names - {"alembic_version"}:
        raise RuntimeError("Baseline refuses nonempty database; never stamp live schemas")
    tables = [t for t in Base.metadata.sorted_tables
              if t.name != "v95_api_rate_counters"]
    if {t.name for t in tables} != BASE_TABLES:
        raise RuntimeError("Model changed since baseline; write a new revision")
    Base.metadata.create_all(bind, tables=tables, checkfirst=False)


def downgrade():
    raise RuntimeError("Downgrade disabled: restore isolated fixture from a backup")
