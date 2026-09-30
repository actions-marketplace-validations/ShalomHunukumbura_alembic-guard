"""add email to users -- the zero-downtime version

Revision ID: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0001"


def upgrade():
    # Nullable first; the app backfills it, and a later migration sets NOT NULL.
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))

    # Built without blocking writes. CONCURRENTLY can't run inside a transaction.
    with op.get_context().autocommit_block():
        op.create_index("ix_users_email", "users", ["email"], unique=True, postgresql_concurrently=True)

    # Skip the full-table check now; VALIDATE CONSTRAINT runs later under a light lock.
    op.create_foreign_key(
        "fk_orders_user", "orders", "users", ["user_id"], ["id"], postgresql_not_valid=True
    )


def downgrade():
    op.drop_constraint("fk_orders_user", "orders")
    op.drop_index("ix_users_email", "users")
    op.drop_column("users", "email")
