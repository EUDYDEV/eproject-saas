"""multi tenant memberships and platform role

Revision ID: 9d1c2b7e8f10
Revises: 4f7b2a9d1c6e
Create Date: 2026-02-15 23:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9d1c2b7e8f10"
down_revision = "4f7b2a9d1c6e"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("branches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("slug", sa.String(length=120), nullable=True))
        batch_op.create_unique_constraint("uq_branches_slug", ["slug"])

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("platform_role", sa.String(length=50), nullable=True))

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False, server_default="STAFF"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "branch_id", name="uq_membership_user_branch"),
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO memberships (user_id, branch_id, role, created_at)
            SELECT u.id, u.branch_id,
                   CASE WHEN u.role = 'FOUNDER' THEN 'OWNER' ELSE 'STAFF' END,
                   CURRENT_TIMESTAMP
            FROM users u
            WHERE u.branch_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = u.id AND m.branch_id = u.branch_id
              )
            """
        )
    )


def downgrade():
    op.drop_table("memberships")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("platform_role")

    with op.batch_alter_table("branches", schema=None) as batch_op:
        batch_op.drop_constraint("uq_branches_slug", type_="unique")
        batch_op.drop_column("slug")
