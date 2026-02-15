"""add invite token link to bookings

Revision ID: f2b8d67a1c3e
Revises: e1a9fd44b2c0
Create Date: 2026-02-14 17:55:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2b8d67a1c3e"
down_revision = "e1a9fd44b2c0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("invite_token_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_bookings_invite_token", "invite_tokens", ["invite_token_id"], ["id"])


def downgrade():
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_constraint("fk_bookings_invite_token", type_="foreignkey")
        batch_op.drop_column("invite_token_id")
