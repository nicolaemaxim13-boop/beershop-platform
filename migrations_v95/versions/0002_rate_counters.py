"""Create the v9.5 DB-backed rate limiter counter table.

Revision ID: 0002_v95
Revises: 0001_v95
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_v95"
down_revision = "0001_v95"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "v95_api_rate_counters",
        sa.Column("key_hash", sa.String(length=64), primary_key=True),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Downgrade disabled: restore isolated fixture from a backup")
