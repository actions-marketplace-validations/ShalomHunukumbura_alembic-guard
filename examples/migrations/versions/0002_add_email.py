"""add email to users -- the migration that looks fine in review

Revision ID: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    op.add_column("users", sa.Column("email", sa.String(255), nullable=False))
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.alter_column("users", "name", new_column_name="full_name")
    op.create_foreign_key("fk_orders_user", "orders", "users", ["user_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_orders_user", "orders")
    op.alter_column("users", "full_name", new_column_name="name")
    op.drop_index("ix_users_email", "users")
    op.drop_column("users", "email")
