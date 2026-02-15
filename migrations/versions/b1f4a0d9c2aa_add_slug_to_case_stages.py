"""add slug to case_stages

Revision ID: b1f4a0d9c2aa
Revises: 9d1c2b7e8f10
Create Date: 2026-02-15 15:55:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1f4a0d9c2aa"
down_revision = "9d1c2b7e8f10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("case_stages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("slug", sa.String(length=120), nullable=True))
        batch_op.create_unique_constraint("uq_case_stages_slug", ["slug"])


def downgrade():
    with op.batch_alter_table("case_stages", schema=None) as batch_op:
        batch_op.drop_constraint("uq_case_stages_slug", type_="unique")
        batch_op.drop_column("slug")
