"""add show social links flag on student cvs

Revision ID: b6e4d14c2f9a
Revises: 9f3d21e8a4b7
Create Date: 2026-02-14 12:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b6e4d14c2f9a"
down_revision = "9f3d21e8a4b7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("student_cvs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("show_social_links", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table("student_cvs", schema=None) as batch_op:
        batch_op.drop_column("show_social_links")
