"""add platform branding fields

Revision ID: c3f2a91d7b11
Revises: b6e4d14c2f9a
Create Date: 2026-02-14 16:15:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c3f2a91d7b11"
down_revision = "b6e4d14c2f9a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("portal_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("site_name", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("site_tagline", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("site_footer_text", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("site_logo_url", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("portal_settings", schema=None) as batch_op:
        batch_op.drop_column("site_logo_url")
        batch_op.drop_column("site_footer_text")
        batch_op.drop_column("site_tagline")
        batch_op.drop_column("site_name")
