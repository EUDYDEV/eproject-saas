"""add user profile fields

Revision ID: d47f2ab9c8ee
Revises: c3f2a91d7b11
Create Date: 2026-02-14 16:45:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d47f2ab9c8ee"
down_revision = "c3f2a91d7b11"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("phone", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("avatar_path", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("email_signature", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("email_signature")
        batch_op.drop_column("avatar_path")
        batch_op.drop_column("phone")
        batch_op.drop_column("display_name")
