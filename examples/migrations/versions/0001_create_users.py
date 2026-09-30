"""create users

Revision ID: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade():
    # Brand-new table: indexes and constraints on it can't block anyone.
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
    )
    op.create_index("ix_users_name", "users", ["name"])


def downgrade():
    op.drop_table("users")
