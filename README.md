# alembic-guard

**Catch Alembic migrations that lock tables or break running code, before they reach production.**

This migration passes code review, passes tests on an empty dev database, and takes down production:

```python
def upgrade():
    op.add_column("users", sa.Column("email", sa.String(255), nullable=False))
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.alter_column("users", "name", new_column_name="full_name")
    op.create_foreign_key("fk_orders_user", "orders", "users", ["user_id"], ["id"])
```

```console
$ alembic-guard
migrations/versions/0002_add_email.py:13:5  AG001 error  Adding NOT NULL column "users.email" without a server_default fails on a table with rows.
    fix: Add a server_default, or add the column as nullable, backfill it, then set NOT NULL in a later migration.
migrations/versions/0002_add_email.py:14:5  AG002 warning  Creating index "ix_users_email" on "users" blocks writes until it is built.
    fix: Pass postgresql_concurrently=True and run it inside `with op.get_context().autocommit_block():`.
migrations/versions/0002_add_email.py:15:5  AG004 error  Renaming "users.name" to "full_name" breaks code still using the old name.
    fix: Expand and contract: add the new column or table, write to both, backfill, move reads over, then drop the old one in a later release.
migrations/versions/0002_add_email.py:16:5  AG008 warning  Foreign key from "orders" to "users" is validated while blocking writes on both tables.
    fix: Pass postgresql_not_valid=True, then run `ALTER TABLE ... VALIDATE CONSTRAINT ...` in a separate migration. Validation only takes a light lock.
Found 2 errors and 2 warnings in 3 migrations.
```

Tools like [squawk](https://squawkhq.com) do this for raw SQL. alembic-guard reads your
**Alembic Python migrations** directly, so it understands `op.add_column`,
`batch_alter_table`, `autocommit_block` and the rest, with no database and no SQL
generation step.

## Install

```bash
pip install alembic-guard      # or: uv tool install alembic-guard
```

## Usage

```bash
alembic-guard                                 # finds every Alembic versions/ dir
alembic-guard migrations/versions             # or point it at files / dirs
alembic-guard --diff-base origin/main         # only migrations this branch added or changed
alembic-guard --strict                        # fail on warnings too
alembic-guard --explain AG003                 # why a rule exists and how to fix it
alembic-guard --format github | json          # CI annotations or machine-readable output
```

Exit code `0` = clean, `1` = findings that fail the run (errors, or anything with `--strict`),
`2` = usage error.

### GitHub Action

Findings show up as annotations on the pull request diff.

```yaml
# .github/workflows/migrations.yml
on: pull_request
jobs:
  alembic-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # needed to diff against the base branch
      - uses: ShalomHunukumbura/alembic-guard@v1
        with:
          strict: "false"        # set "true" to fail on warnings
          # paths: "app/migrations/versions"
          # ignore: "AG005"
```

By default only migrations changed in the PR are checked, so adopting it on a repo
with years of history doesn't flood you with old findings.

## Rules

| ID | Severity | Catches |
|----|----------|---------|
| AG001 | error | `NOT NULL` column added without a `server_default`: fails on any table with rows |
| AG002 | warning | Index created without `CONCURRENTLY`: blocks writes while it builds |
| AG003 | error | `CONCURRENTLY` inside Alembic's transaction: Postgres rejects it |
| AG004 | error | Table or column rename: old app version breaks mid-deploy |
| AG005 | warning | Table or column drop: running code that still reads it breaks |
| AG006 | warning | Column type change: usually rewrites the table under an exclusive lock |
| AG007 | warning | `SET NOT NULL` on an existing column: full scan under an exclusive lock |
| AG008 | warning | Foreign key or check constraint without `NOT VALID`: validated while blocking writes |
| AG009 | warning | Column added with a volatile default (`gen_random_uuid()`, `nextval`): table rewrite |
| AG010 | warning | Unique or primary key constraint on an existing table: builds an index while blocking writes |

Operations on a table **created earlier in the same migration** are not flagged,
since nobody can be using it yet. Raw SQL in `op.execute("...")` is checked for
the common cases too.

## Silencing a finding

Sometimes you know better (the table has 12 rows, or it's a maintenance window):

```python
op.drop_column("users", "legacy_flag")  # alembic-guard: ignore[AG005]

# alembic-guard: ignore
op.rename_table("tmp_import", "imports")
```

Or project-wide in `pyproject.toml`:

```toml
[tool.alembic-guard]
paths = ["app/migrations/versions"]
ignore = ["AG005"]
strict = false
include-downgrade = false
```

## How it works

Migrations are parsed with Python's `ast` module and never imported or executed.
The checker walks `upgrade()` in order, keeping track of three things: which
tables were created in this migration, which variables are `batch_alter_table`
contexts (and for which table), and whether it's inside an `autocommit_block()`.
Each `op.*` call is matched against Alembic's real signatures, so positional and
keyword arguments are both understood.

The rules target **PostgreSQL**, where these locking behaviours are well documented.

## Development

```bash
uv sync
uv run pytest
uv run alembic-guard examples/migrations/versions
```

## License

MIT
