"""Walk an Alembic migration's syntax tree and report risky operations.

Nothing is imported or executed: the migration is parsed with `ast`, so the
checker works without a database, a virtualenv with your models, or Alembic.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

from .rules import RULES, Rule, Severity


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    col: int
    rule_id: str
    message: str

    @property
    def rule(self) -> Rule:
        return RULES[self.rule_id]

    @property
    def severity(self) -> Severity:
        return self.rule.severity


# Defaults that produce a different value per row force a full table rewrite.
_VOLATILE_DEFAULTS = (
    "gen_random_uuid",
    "uuid_generate_v1",
    "uuid_generate_v4",
    "random(",
    "clock_timestamp",
    "timeofday",
    "nextval",
)

# Where each op.* function takes its table argument: (position, keyword name).
# batch_op.* calls omit that argument, so later positions shift left by one.
_TABLE_ARG: dict[str, tuple[int, str] | None] = {
    "create_table": (0, "table_name"),
    "add_column": (0, "table_name"),
    "alter_column": (0, "table_name"),
    "drop_column": (0, "table_name"),
    "drop_table": (0, "table_name"),
    "rename_table": (0, "old_table_name"),
    "create_index": (1, "table_name"),
    "create_foreign_key": (1, "source_table"),
    "create_unique_constraint": (1, "table_name"),
    "create_primary_key": (1, "table_name"),
    "create_check_constraint": (1, "table_name"),
    "execute": None,
}

_SUPPRESS = re.compile(r"#\s*alembic-guard:\s*ignore(?:\[(?P<ids>[A-Za-z0-9_,\s]+)\])?")
_ALL_RULES = "*"


def check_file(path: str | Path, include_downgrade: bool = False) -> list[Finding]:
    path = Path(path)
    return check_source(path.read_text(encoding="utf-8"), str(path), include_downgrade)


def check_source(
    source: str, path: str = "<migration>", include_downgrade: bool = False
) -> list[Finding]:
    tree = ast.parse(source, filename=path)
    suppressions = _suppressions(source)
    findings: list[Finding] = []

    for func in tree.body:
        if not isinstance(func, ast.FunctionDef) or not _is_checked(func.name, include_downgrade):
            continue
        visitor = _MigrationVisitor()
        for stmt in func.body:
            visitor.visit(stmt)
        for node, rule_id, message in visitor.hits:
            if not _is_suppressed(node, rule_id, suppressions):
                findings.append(Finding(path, node.lineno, node.col_offset + 1, rule_id, message))

    return sorted(findings, key=lambda f: (f.line, f.col, f.rule_id))


def _is_checked(name: str, include_downgrade: bool) -> bool:
    # Multi-database templates generate upgrade_<engine>() functions.
    if name == "upgrade" or name.startswith("upgrade_"):
        return True
    return include_downgrade and (name == "downgrade" or name.startswith("downgrade_"))


@dataclass(frozen=True)
class _Suppression:
    rule_ids: set[str]
    own_line: bool  # a comment on its own line covers the statement below it


def _suppressions(source: str) -> dict[int, _Suppression]:
    """Map line number -> the `# alembic-guard: ignore[...]` comment on that line."""
    result: dict[int, _Suppression] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type != tokenize.COMMENT:
                continue
            match = _SUPPRESS.search(tok.string)
            if match:
                ids = match.group("ids")
                rule_ids = {i.strip().upper() for i in ids.split(",") if i.strip()} if ids else {_ALL_RULES}
                result[tok.start[0]] = _Suppression(rule_ids, own_line=tok.line.lstrip().startswith("#"))
    except tokenize.TokenError:
        pass
    return result


def _is_suppressed(node: ast.AST, rule_id: str, suppressions: dict[int, _Suppression]) -> bool:
    end = getattr(node, "end_lineno", None) or node.lineno
    for line in range(node.lineno - 1, end + 1):
        supp = suppressions.get(line)
        if supp is None or (line < node.lineno and not supp.own_line):
            continue
        if _ALL_RULES in supp.rule_ids or rule_id in supp.rule_ids:
            return True
    return False


class _OpCall:
    """An `op.<name>(...)` or `batch_op.<name>(...)` call with signature-aware argument lookup."""

    def __init__(self, node: ast.Call, batch_table: str | None, is_batch: bool):
        self.node = node
        self.name = node.func.attr  # type: ignore[attr-defined]
        self.is_batch = is_batch
        self._table_arg = _TABLE_ARG[self.name]
        self._batch_table = batch_table

    @property
    def table(self) -> str | None:
        if self.is_batch:
            return self._batch_table
        if self._table_arg is None:
            return None
        return _str(_arg(self.node, *self._table_arg))

    def arg(self, keyword: str, position: int) -> ast.expr | None:
        """Look up an argument by its position in the op.* signature."""
        if self.is_batch and self._table_arg is not None and position > self._table_arg[0]:
            position -= 1
        return _arg(self.node, position, keyword)

    def kw(self, keyword: str) -> ast.expr | None:
        return _kw(self.node, keyword)


class _MigrationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[tuple[ast.AST, str, str]] = []
        # Tables created earlier in this migration are empty and unused, so most
        # locking concerns don't apply to them.
        self.new_tables: set[str] = set()
        self.batch_aliases: dict[str, str | None] = {}
        self.autocommit_depth = 0

    def report(self, node: ast.AST, rule_id: str, message: str) -> None:
        self.hits.append((node, rule_id, message))

    def is_new(self, table: str | None) -> bool:
        return table is not None and _norm(table) in self.new_tables

    def visit_With(self, node: ast.With) -> None:
        aliases: list[str] = []
        autocommit = 0
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if (
                call.func.attr == "batch_alter_table"
                and _receiver(call) == "op"
                and isinstance(item.optional_vars, ast.Name)
            ):
                aliases.append(item.optional_vars.id)
                self.batch_aliases[item.optional_vars.id] = _str(_arg(call, 0, "table_name"))
            elif call.func.attr == "autocommit_block":
                autocommit += 1

        self.autocommit_depth += autocommit
        self.generic_visit(node)
        self.autocommit_depth -= autocommit
        for alias in aliases:
            self.batch_aliases.pop(alias, None)

    def visit_Call(self, node: ast.Call) -> None:
        receiver = _receiver(node)
        if receiver == "op" or receiver in self.batch_aliases:
            name = node.func.attr  # type: ignore[attr-defined]
            handler = getattr(self, f"_check_{name}", None)
            if handler is not None:
                is_batch = receiver != "op"
                handler(_OpCall(node, self.batch_aliases.get(receiver), is_batch))
        self.generic_visit(node)

    # --- op handlers -----------------------------------------------------

    def _check_create_table(self, c: _OpCall) -> None:
        if c.table:
            self.new_tables.add(_norm(c.table))

    def _check_add_column(self, c: _OpCall) -> None:
        column = c.arg("column", 1)
        if not isinstance(column, ast.Call) or _callee_name(column) != "Column" or self.is_new(c.table):
            return
        col_name = _str(column.args[0]) if column.args else None
        nullable = _kw(column, "nullable")
        server_default = _kw(column, "server_default")
        not_null = _is_false(nullable) or (nullable is None and _is_true(_kw(column, "primary_key")))
        label = _label(c.table, col_name)

        if not_null and server_default is None:
            self.report(
                c.node,
                "AG001",
                f"Adding NOT NULL column {label} without a server_default fails on a table with rows.",
            )
        if server_default is not None and _is_volatile(server_default):
            self.report(
                c.node,
                "AG009",
                f"Adding {label} with volatile default `{ast.unparse(server_default)}` "
                "rewrites the whole table under an exclusive lock.",
            )

    def _check_create_index(self, c: _OpCall) -> None:
        index = _str(c.arg("index_name", 0)) or "index"
        on = f" on {_q(c.table)}" if c.table else ""
        if _is_true(c.kw("postgresql_concurrently")):
            if self.autocommit_depth == 0:
                self.report(
                    c.node,
                    "AG003",
                    f"Concurrent index {_q(index)}{on} is inside Alembic's transaction and will fail.",
                )
        elif not self.is_new(c.table):
            self.report(c.node, "AG002", f"Creating index {_q(index)}{on} blocks writes until it is built.")

    def _check_alter_column(self, c: _OpCall) -> None:
        if self.is_new(c.table):
            return
        label = _label(c.table, _str(c.arg("column_name", 1)))
        new_name = _str(c.kw("new_column_name"))
        if new_name is not None:
            self.report(c.node, "AG004", f"Renaming {label} to {_q(new_name)} breaks code still using the old name.")
        type_ = c.kw("type_")
        if type_ is not None:
            self.report(
                c.node,
                "AG006",
                f"Changing the type of {label} to `{ast.unparse(type_)}` may rewrite the table under an exclusive lock.",
            )
        if _is_false(c.kw("nullable")):
            self.report(
                c.node, "AG007", f"Setting {label} to NOT NULL scans the whole table under an exclusive lock."
            )

    def _check_drop_column(self, c: _OpCall) -> None:
        if self.is_new(c.table):
            return
        label = _label(c.table, _str(c.arg("column_name", 1)))
        self.report(c.node, "AG005", f"Dropping {label} breaks any running code that still reads it.")

    def _check_drop_table(self, c: _OpCall) -> None:
        if c.table is not None and self.is_new(c.table):
            self.new_tables.discard(_norm(c.table))
            return
        self.report(c.node, "AG005", f"Dropping table {_q(c.table)} breaks any running code that still uses it.")

    def _check_rename_table(self, c: _OpCall) -> None:
        new_name = _str(c.arg("new_table_name", 1))
        self.report(
            c.node,
            "AG004",
            f"Renaming table {_q(c.table)} to {_q(new_name)} breaks code still using the old name.",
        )

    def _check_create_foreign_key(self, c: _OpCall) -> None:
        if self.is_new(c.table) or _is_true(c.kw("postgresql_not_valid")):
            return
        referent = _str(c.arg("referent_table", 2))
        self.report(
            c.node,
            "AG008",
            f"Foreign key from {_q(c.table)} to {_q(referent)} is validated while blocking writes on both tables.",
        )

    def _check_create_check_constraint(self, c: _OpCall) -> None:
        if self.is_new(c.table) or _is_true(c.kw("postgresql_not_valid")):
            return
        name = _str(c.arg("constraint_name", 0)) or "constraint"
        self.report(
            c.node, "AG008", f"Check constraint {_q(name)} on {_q(c.table)} is validated while blocking writes."
        )

    def _check_create_unique_constraint(self, c: _OpCall) -> None:
        if not self.is_new(c.table):
            self.report(c.node, "AG010", f"Adding a unique constraint to {_q(c.table)} builds an index while blocking writes.")

    def _check_create_primary_key(self, c: _OpCall) -> None:
        if not self.is_new(c.table):
            self.report(c.node, "AG010", f"Adding a primary key to {_q(c.table)} builds an index while blocking writes.")

    def _check_execute(self, c: _OpCall) -> None:
        sql = _sql_text(c.arg("sqltext", 0))
        if sql is None:
            return
        for statement in sql.split(";"):
            self._check_raw_sql(c.node, " ".join(statement.split()))

    def _check_raw_sql(self, node: ast.Call, stmt: str) -> None:
        upper = stmt.upper()
        if not upper:
            return

        if re.match(r"CREATE\s+(UNIQUE\s+)?INDEX\b", upper):
            table = _sql_table(stmt, r"\bON\s+(?:ONLY\s+)?")
            if "CONCURRENTLY" in upper:
                if self.autocommit_depth == 0:
                    self.report(node, "AG003", "Raw CREATE INDEX CONCURRENTLY is inside Alembic's transaction and will fail.")
            elif not self.is_new(table):
                self.report(node, "AG002", f"Raw CREATE INDEX on {_q(table)} blocks writes until it is built.")
            return

        if not upper.startswith("ALTER TABLE"):
            return
        table = _sql_table(stmt, r"^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?")
        if self.is_new(table):
            return
        if re.search(r"\bRENAME\b", upper):
            self.report(node, "AG004", f"Raw rename on {_q(table)} breaks code still using the old name.")
        if re.search(r"\bADD\s+CONSTRAINT\b.*\b(FOREIGN\s+KEY|CHECK)\b", upper) and "NOT VALID" not in upper:
            self.report(node, "AG008", f"Raw constraint on {_q(table)} is validated while blocking writes. Add NOT VALID.")
        if re.search(r"\bSET\s+NOT\s+NULL\b", upper):
            self.report(node, "AG007", f"Raw SET NOT NULL on {_q(table)} scans the table under an exclusive lock.")
        if re.search(r"\bDROP\s+COLUMN\b", upper):
            self.report(node, "AG005", f"Raw DROP COLUMN on {_q(table)} breaks any running code that still reads it.")


# --- AST helpers -----------------------------------------------------------


def _receiver(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _arg(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    value = _kw(call, keyword)
    if value is not None:
        return value
    positional = call.args[: position + 1]
    if len(positional) > position and not any(isinstance(a, ast.Starred) for a in positional):
        return call.args[position]
    return None


def _str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _is_volatile(node: ast.expr) -> bool:
    text = ast.unparse(node).lower()
    return any(fn in text for fn in _VOLATILE_DEFAULTS)


def _sql_text(node: ast.expr | None) -> str | None:
    """The SQL string from `"..."` or `sa.text("...")`, if it is a literal."""
    if isinstance(node, ast.Call) and _callee_name(node) == "text" and node.args:
        node = node.args[0]
    return _str(node)


def _sql_table(stmt: str, prefix: str) -> str | None:
    match = re.search(prefix + r'("?[\w.]+"?)', stmt, re.IGNORECASE)
    return match.group(1).strip('"') if match else None


def _norm(table: str) -> str:
    return table.strip('"').lower()


def _q(name: str | None) -> str:
    return f'"{name}"' if name else "<dynamic>"


def _label(table: str | None, column: str | None) -> str:
    if table and column:
        return f'"{table}.{column}"'
    return _q(column or table)
