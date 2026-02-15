"""add student document folders

Revision ID: e1a9fd44b2c0
Revises: d47f2ab9c8ee
Create Date: 2026-02-14 17:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e1a9fd44b2c0"
down_revision = "d47f2ab9c8ee"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "student_document_folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("folder_name", sa.String(length=40), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", "folder_name", name="uq_student_folder_name"),
    )


def downgrade():
    op.drop_table("student_document_folders")
