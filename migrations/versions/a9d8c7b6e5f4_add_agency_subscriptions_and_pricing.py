"""add agency subscriptions and pricing

Revision ID: a9d8c7b6e5f4
Revises: f2b8d67a1c3e
Create Date: 2026-02-14 18:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a9d8c7b6e5f4"
down_revision = "f2b8d67a1c3e"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("portal_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("plan_starter_price", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("plan_pro_price", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("plan_enterprise_price", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("plan_currency", sa.String(length=10), nullable=False, server_default="XOF"))
        batch_op.add_column(sa.Column("payment_link", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("billing_sender_email", sa.String(length=255), nullable=True, server_default="eudyproject@gmail.com"))
        batch_op.add_column(sa.Column("expiry_notice_days", sa.Integer(), nullable=False, server_default="7"))

    op.create_table(
        "agency_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("plan_code", sa.String(length=30), nullable=False, server_default="starter"),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="XOF"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("payment_reference", sa.String(length=255), nullable=True),
        sa.Column("last_warning_sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_expired_sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("branch_id"),
    )


def downgrade():
    op.drop_table("agency_subscriptions")
    with op.batch_alter_table("portal_settings", schema=None) as batch_op:
        batch_op.drop_column("expiry_notice_days")
        batch_op.drop_column("billing_sender_email")
        batch_op.drop_column("payment_link")
        batch_op.drop_column("plan_currency")
        batch_op.drop_column("plan_enterprise_price")
        batch_op.drop_column("plan_pro_price")
        batch_op.drop_column("plan_starter_price")
