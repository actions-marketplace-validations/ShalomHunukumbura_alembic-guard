"""The rule catalogue: what each check means, why it matters and how to fix it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    severity: Severity
    summary: str
    why: str
    fix: str


_ALL = [
    Rule(
        id="AG001",
        name="add-not-null-column",
        severity=Severity.ERROR,
        summary="NOT NULL column added without a server_default.",
        why=(
            "Postgres has to fill the new column for every existing row. With no "
            "server_default there is nothing to fill it with, so the migration fails "
            "as soon as the table has data. A Python-side `default=` does not help: "
            "the database never sees it."
        ),
        fix=(
            "Add a server_default, or add the column as nullable, backfill it, then "
            "set NOT NULL in a later migration."
        ),
    ),
    Rule(
        id="AG002",
        name="index-not-concurrent",
        severity=Severity.WARNING,
        summary="Index created without CONCURRENTLY.",
        why=(
            "A plain CREATE INDEX blocks every INSERT, UPDATE and DELETE on the table "
            "until the index is built. On a large table that can be minutes of "
            "failed writes."
        ),
        fix=(
            "Pass postgresql_concurrently=True and run it inside "
            "`with op.get_context().autocommit_block():`."
        ),
    ),
    Rule(
        id="AG003",
        name="concurrent-index-in-transaction",
        severity=Severity.ERROR,
        summary="CONCURRENTLY used inside a transaction.",
        why=(
            "Postgres refuses to run CREATE INDEX CONCURRENTLY inside a transaction "
            "block, and Alembic wraps every Postgres migration in one. The migration "
            "will fail at deploy time."
        ),
        fix="Wrap the operation in `with op.get_context().autocommit_block():`.",
    ),
    Rule(
        id="AG004",
        name="rename",
        severity=Severity.ERROR,
        summary="Table or column renamed.",
        why=(
            "During a deploy, the old version of your app keeps running until the new "
            "one is up. It still queries the old name and starts failing the moment "
            "the rename commits."
        ),
        fix=(
            "Expand and contract: add the new column or table, write to both, "
            "backfill, move reads over, then drop the old one in a later release."
        ),
    ),
    Rule(
        id="AG005",
        name="drop",
        severity=Severity.WARNING,
        summary="Table or column dropped.",
        why=(
            "If any running code (or an ORM model that selects every mapped column) "
            "still references it, those requests fail as soon as the drop commits. "
            "The data is also gone for good."
        ),
        fix=(
            "Remove every use from the code and ship that first. Drop the column or "
            "table in a later release."
        ),
    ),
    Rule(
        id="AG006",
        name="column-type-change",
        severity=Severity.WARNING,
        summary="Column type changed.",
        why=(
            "Most type changes make Postgres rewrite the whole table while holding "
            "an ACCESS EXCLUSIVE lock, which blocks reads and writes. A few changes "
            "(such as widening a varchar) are safe, most are not."
        ),
        fix=(
            "Add a new column with the new type, backfill it, switch the code over, "
            "then drop the old column. Or confirm the change is metadata-only and "
            "suppress this warning."
        ),
    ),
    Rule(
        id="AG007",
        name="set-not-null",
        severity=Severity.WARNING,
        summary="Existing column changed to NOT NULL.",
        why=(
            "SET NOT NULL scans the whole table while holding an ACCESS EXCLUSIVE "
            "lock, so nothing can read or write it until the scan finishes."
        ),
        fix=(
            "Add a CHECK (col IS NOT NULL) NOT VALID constraint, VALIDATE it in a "
            "separate step, then SET NOT NULL (Postgres 12+ skips the scan) and drop "
            "the check."
        ),
    ),
    Rule(
        id="AG008",
        name="constraint-validated-under-lock",
        severity=Severity.WARNING,
        summary="Foreign key or check constraint validated while locking the table.",
        why=(
            "Adding the constraint checks every existing row while blocking writes "
            "(for a foreign key, on both tables)."
        ),
        fix=(
            "Pass postgresql_not_valid=True, then run "
            "`ALTER TABLE ... VALIDATE CONSTRAINT ...` in a separate migration. "
            "Validation only takes a light lock."
        ),
    ),
    Rule(
        id="AG009",
        name="volatile-default",
        severity=Severity.WARNING,
        summary="Column added with a volatile server_default.",
        why=(
            "Since Postgres 11, a constant default is stored once and no rows are "
            "touched. A volatile default like gen_random_uuid() or nextval() has a "
            "different value per row, so Postgres rewrites the whole table under an "
            "ACCESS EXCLUSIVE lock."
        ),
        fix=(
            "Add the column without a default, backfill it in batches, then set the "
            "default for new rows."
        ),
    ),
    Rule(
        id="AG010",
        name="unique-or-primary-key-constraint",
        severity=Severity.WARNING,
        summary="Unique or primary key constraint added to an existing table.",
        why=(
            "Adding the constraint builds its index while blocking writes, just like "
            "a non-concurrent CREATE INDEX."
        ),
        fix=(
            "Create a unique index CONCURRENTLY first, then attach it with "
            "`ALTER TABLE ... ADD CONSTRAINT ... UNIQUE USING INDEX ...`."
        ),
    ),
]

RULES: dict[str, Rule] = {rule.id: rule for rule in _ALL}
