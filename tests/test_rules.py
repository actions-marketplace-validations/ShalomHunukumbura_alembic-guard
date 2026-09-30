from textwrap import dedent

import pytest

from alembic_guard import check_source


def rules(body: str, downgrade: str = "pass", include_downgrade: bool = False) -> list[str]:
    source = (
        "from alembic import op\nimport sqlalchemy as sa\n\n\n"
        "def upgrade():\n" + _indent(body) + "\n\ndef downgrade():\n" + _indent(downgrade) + "\n"
    )
    return [f.rule_id for f in check_source(source, include_downgrade=include_downgrade)]


def _indent(code: str) -> str:
    return "\n".join("    " + line for line in dedent(code).strip().splitlines())


# --- AG001 add-not-null-column ------------------------------------------------


def test_not_null_column_without_default_is_an_error():
    assert rules('op.add_column("users", sa.Column("email", sa.String(), nullable=False))') == ["AG001"]


def test_not_null_column_with_server_default_is_fine():
    assert rules(
        'op.add_column("users", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))'
    ) == []


def test_python_side_default_does_not_count():
    assert rules('op.add_column("users", sa.Column("n", sa.Integer(), nullable=False, default=0))') == ["AG001"]


def test_nullable_column_is_fine():
    assert rules('op.add_column("users", sa.Column("bio", sa.Text()))') == []


def test_primary_key_column_is_implicitly_not_null():
    assert rules('op.add_column("users", sa.Column("uid", sa.Integer(), primary_key=True))') == ["AG001"]


def test_keyword_arguments_are_understood():
    assert rules(
        'op.add_column(table_name="users", column=sa.Column("email", sa.String(), nullable=False))'
    ) == ["AG001"]


def test_batch_add_column():
    assert rules(
        """
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(sa.Column("email", sa.String(), nullable=False))
        """
    ) == ["AG001"]


# --- AG002 / AG003 indexes -----------------------------------------------------


def test_plain_index_on_existing_table_warns():
    assert rules('op.create_index("ix_users_email", "users", ["email"])') == ["AG002"]


def test_concurrent_index_outside_autocommit_block_fails():
    assert rules(
        'op.create_index("ix_users_email", "users", ["email"], postgresql_concurrently=True)'
    ) == ["AG003"]


def test_concurrent_index_inside_autocommit_block_is_fine():
    assert rules(
        """
        with op.get_context().autocommit_block():
            op.create_index("ix_users_email", "users", ["email"], postgresql_concurrently=True)
        """
    ) == []


def test_index_on_table_created_in_same_migration_is_fine():
    assert rules(
        """
        op.create_table("posts", sa.Column("id", sa.Integer(), primary_key=True))
        op.create_index("ix_posts_id", "posts", ["id"])
        """
    ) == []


def test_index_before_create_table_still_counts_as_existing():
    assert rules(
        """
        op.create_index("ix_posts_id", "posts", ["id"])
        op.create_table("posts", sa.Column("id", sa.Integer(), primary_key=True))
        """
    ) == ["AG002"]


def test_batch_create_index_uses_batch_table():
    assert rules(
        """
        with op.batch_alter_table("users") as batch_op:
            batch_op.create_index("ix_users_email", ["email"])
        """
    ) == ["AG002"]


# --- AG004 rename / AG005 drop -----------------------------------------------


def test_rename_column():
    assert rules('op.alter_column("users", "name", new_column_name="full_name")') == ["AG004"]


def test_rename_table():
    assert rules('op.rename_table("users", "accounts")') == ["AG004"]


def test_drop_column_and_table():
    assert rules(
        """
        op.drop_column("users", "legacy")
        op.drop_table("old_things")
        """
    ) == ["AG005", "AG005"]


def test_drop_of_table_created_in_same_migration_is_fine():
    assert rules(
        """
        op.create_table("tmp", sa.Column("id", sa.Integer()))
        op.drop_table("tmp")
        """
    ) == []


# --- AG006 / AG007 alter_column ----------------------------------------------


def test_type_change():
    assert rules('op.alter_column("users", "age", type_=sa.BigInteger())') == ["AG006"]


def test_set_not_null():
    assert rules('op.alter_column("users", "email", nullable=False)') == ["AG007"]


def test_setting_nullable_true_is_fine():
    assert rules('op.alter_column("users", "email", nullable=True)') == []


def test_batch_alter_column_combines_rules():
    assert rules(
        """
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("age", type_=sa.BigInteger(), nullable=False)
        """
    ) == ["AG006", "AG007"]


# --- AG008 constraints --------------------------------------------------------


def test_foreign_key_without_not_valid():
    assert rules('op.create_foreign_key("fk", "orders", "users", ["user_id"], ["id"])') == ["AG008"]


def test_foreign_key_not_valid_is_fine():
    assert rules(
        'op.create_foreign_key("fk", "orders", "users", ["user_id"], ["id"], postgresql_not_valid=True)'
    ) == []


def test_check_constraint():
    assert rules('op.create_check_constraint("ck_age", "users", "age >= 0")') == ["AG008"]


# --- AG009 volatile default --------------------------------------------------


@pytest.mark.parametrize(
    "default", ['sa.text("gen_random_uuid()")', "sa.func.uuid_generate_v4()", '"nextval(\'seq\')"']
)
def test_volatile_default(default):
    assert rules(f'op.add_column("users", sa.Column("uid", sa.UUID(), nullable=False, server_default={default}))') == [
        "AG009"
    ]


def test_now_is_not_volatile():
    assert rules(
        'op.add_column("users", sa.Column("created", sa.DateTime(), nullable=False, server_default=sa.func.now()))'
    ) == []


# --- AG010 unique / primary key ----------------------------------------------


def test_unique_constraint_on_existing_table():
    assert rules('op.create_unique_constraint("uq_email", "users", ["email"])') == ["AG010"]


def test_primary_key_on_existing_table():
    assert rules('op.create_primary_key("pk_users", "users", ["id"])') == ["AG010"]


# --- raw SQL -------------------------------------------------------------------


def test_raw_sql_index_and_rename():
    assert rules(
        """
        op.execute("CREATE INDEX ix_users_email ON users (email)")
        op.execute(sa.text("ALTER TABLE users RENAME COLUMN name TO full_name"))
        """
    ) == ["AG002", "AG004"]


def test_raw_sql_multiple_statements():
    assert rules(
        'op.execute("ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (user_id) REFERENCES users (id); '
        'ALTER TABLE orders ALTER COLUMN user_id SET NOT NULL")'
    ) == ["AG007", "AG008"]


def test_raw_sql_safe_patterns():
    assert rules(
        """
        op.execute("ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID")
        op.execute("ALTER TABLE orders VALIDATE CONSTRAINT fk")
        op.execute("UPDATE users SET active = true")
        with op.get_context().autocommit_block():
            op.execute("CREATE INDEX CONCURRENTLY ix ON users (email)")
        """
    ) == []


def test_raw_sql_concurrent_index_in_transaction():
    assert rules('op.execute("CREATE UNIQUE INDEX CONCURRENTLY ix ON users (email)")') == ["AG003"]


# --- scope and suppressions ----------------------------------------------------


def test_downgrade_is_ignored_by_default():
    assert rules("pass", downgrade='op.drop_column("users", "email")') == []


def test_downgrade_can_be_included():
    assert rules("pass", downgrade='op.drop_column("users", "email")', include_downgrade=True) == ["AG005"]


def test_inline_suppression_for_one_rule():
    assert rules(
        """
        op.drop_column("users", "legacy")  # alembic-guard: ignore[AG005]
        op.drop_column("users", "other")
        """
    ) == ["AG005"]


def test_suppression_on_line_above():
    assert rules(
        """
        # alembic-guard: ignore
        op.rename_table("users", "accounts")
        """
    ) == []


def test_suppression_for_other_rule_does_not_apply():
    assert rules('op.rename_table("users", "accounts")  # alembic-guard: ignore[AG005]') == ["AG004"]


def test_other_receivers_are_ignored():
    assert rules('some_helper.drop_column("users", "legacy")') == []


def test_finding_location_and_message():
    source = 'from alembic import op\n\ndef upgrade():\n    op.drop_column("users", "legacy")\n'
    [finding] = check_source(source, "0001_x.py")
    assert (finding.path, finding.line, finding.col) == ("0001_x.py", 4, 5)
    assert '"users.legacy"' in finding.message
