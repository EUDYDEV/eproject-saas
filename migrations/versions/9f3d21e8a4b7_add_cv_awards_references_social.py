"""add cv awards references social

Revision ID: 9f3d21e8a4b7
Revises: 7b1f3d9ab2c1
Create Date: 2026-02-14 10:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9f3d21e8a4b7"
down_revision = "7b1f3d9ab2c1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("student_cvs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("awards", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("references_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("social_links", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("student_cvs", schema=None) as batch_op:
        batch_op.drop_column("social_links")
        batch_op.drop_column("references_text")
        batch_op.drop_column("awards")
