"""add plan specific payment links

Revision ID: 4f7b2a9d1c6e
Revises: a9d8c7b6e5f4
Create Date: 2026-02-14 20:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4f7b2a9d1c6e"
down_revision = "a9d8c7b6e5f4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("portal_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("payment_link_starter", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("payment_link_pro", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("payment_link_enterprise", sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table("portal_settings", schema=None) as batch_op:
        batch_op.drop_column("payment_link_enterprise")
        batch_op.drop_column("payment_link_pro")
        batch_op.drop_column("payment_link_starter")

