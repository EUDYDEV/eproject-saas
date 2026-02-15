"""add student cvs

Revision ID: 7b1f3d9ab2c1
Revises: 20e72de734bf
Create Date: 2026-02-13 11:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7b1f3d9ab2c1"
down_revision = "20e72de734bf"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "student_cvs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("profile_text", sa.Text(), nullable=True),
        sa.Column("contact_details", sa.Text(), nullable=True),
        sa.Column("hobbies", sa.Text(), nullable=True),
        sa.Column("languages", sa.Text(), nullable=True),
        sa.Column("skills", sa.Text(), nullable=True),
        sa.Column("education", sa.Text(), nullable=True),
        sa.Column("professional_experience", sa.Text(), nullable=True),
        sa.Column("extra_experience", sa.Text(), nullable=True),
        sa.Column("software", sa.Text(), nullable=True),
        sa.Column("show_hobbies", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_languages", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_skills", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_education", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_professional_experience", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_extra_experience", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("show_software", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id"),
    )


def downgrade():
    op.drop_table("student_cvs")
