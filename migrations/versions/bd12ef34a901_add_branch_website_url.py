"""add branch website url

Revision ID: bd12ef34a901
Revises: aa21f7c1d902
Create Date: 2026-02-15 11:45:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "bd12ef34a901"
down_revision = "aa21f7c1d902"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("branches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("website_url", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("branches", schema=None) as batch_op:
        batch_op.drop_column("website_url")
