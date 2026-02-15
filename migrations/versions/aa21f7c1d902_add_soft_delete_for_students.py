"""add soft delete for students

Revision ID: aa21f7c1d902
Revises: 9d1c2b7e8f10
Create Date: 2026-02-16 00:05:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "aa21f7c1d902"
down_revision = "9d1c2b7e8f10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("students", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("students", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
