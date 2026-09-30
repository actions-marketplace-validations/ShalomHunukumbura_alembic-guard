"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from . import __version__
from .checker import Finding, check_file
from .config import load_config
from .report import format_github, format_json, format_text
from .rules import RULES, Severity

_SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "site-packages", "__pycache__", ".tox"}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        for rule in RULES.values():
            print(f"{rule.id}  {rule.severity.value:<7}  {rule.name:<34} {rule.summary}")
        return 0
    if args.explain:
        return _explain(args.explain.upper())

    config = load_config(Path.cwd())
    ignore = config.ignore | {r.strip().upper() for r in (args.ignore or "").split(",") if r.strip()}
    unknown = ignore - RULES.keys()
    if unknown:
        print(f"alembic-guard: unknown rule id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    strict = args.strict or config.strict
    include_downgrade = args.include_downgrade or config.include_downgrade

    paths = args.paths or config.paths or [str(p) for p in _discover_versions_dirs(Path.cwd())]
    if not paths:
        print(
            "alembic-guard: no Alembic versions/ directory found. Pass a path, e.g. "
            "`alembic-guard migrations/versions`.",
            file=sys.stderr,
        )
        return 2

    try:
        files = _collect_files(paths)
        if args.diff_base:
            files = _only_changed(files, args.diff_base)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"alembic-guard: {exc}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    for file in files:
        try:
            findings.extend(check_file(file, include_downgrade))
        except SyntaxError as exc:
            print(f"alembic-guard: could not parse {file}: {exc}", file=sys.stderr)
            return 2
    findings = [f for f in findings if f.rule_id not in ignore]

    if args.format == "github":
        output = format_github(findings)
        # Also print the readable report so the job log makes sense on its own.
        print(format_text(findings, len(files)))
    elif args.format == "json":
        output = format_json(findings)
    else:
        output = format_text(findings, len(files), color=_use_color())
    if output:
        print(output)

    failing = [f for f in findings if strict or f.severity is Severity.ERROR]
    return 1 if failing else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alembic-guard",
        description="Catch Alembic migrations that lock tables or break running code.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Migration files or directories (default: every Alembic versions/ directory found).",
    )
    parser.add_argument("--format", choices=["text", "github", "json"], default="text")
    parser.add_argument("--strict", action="store_true", help="Fail on warnings as well as errors.")
    parser.add_argument("--ignore", metavar="IDS", help="Comma-separated rule ids to skip, e.g. AG002,AG005.")
    parser.add_argument(
        "--diff-base",
        metavar="REF",
        help="Only check migrations added or changed since this git ref (e.g. origin/main).",
    )
    parser.add_argument("--include-downgrade", action="store_true", help="Also check downgrade().")
    parser.add_argument("--list-rules", action="store_true", help="List all rules and exit.")
    parser.add_argument("--explain", metavar="ID", help="Explain one rule in detail and exit.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _explain(rule_id: str) -> int:
    rule = RULES.get(rule_id)
    if rule is None:
        print(f"alembic-guard: unknown rule {rule_id}. Try --list-rules.", file=sys.stderr)
        return 2
    wrap = lambda text: textwrap.fill(text, width=80, initial_indent="  ", subsequent_indent="  ")  # noqa: E731
    print(f"{rule.id} {rule.name} ({rule.severity.value})\n\n{wrap(rule.summary)}\n")
    print(f"Why it matters:\n{wrap(rule.why)}\n\nHow to fix it:\n{wrap(rule.fix)}")
    return 0


def _discover_versions_dirs(root: Path) -> list[Path]:
    """Alembic projects have an env.py next to a versions/ directory."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        if "env.py" in filenames and "versions" in dirnames:
            found.append(Path(dirpath) / "versions")
    return sorted(found)


def _collect_files(paths: list[str]) -> list[Path]:
    files: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(
                p
                for p in path.rglob("*.py")
                if p.name != "__init__.py" and not _SKIP_DIRS.intersection(p.parts)
            )
        else:
            raise FileNotFoundError(f"path does not exist: {raw}")
    return sorted(_relative(f) for f in files)


def _relative(path: Path) -> Path:
    """Show paths relative to the working directory, as GitHub annotations expect."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return path


def _only_changed(files: list[Path], base: str) -> list[Path]:
    def git(*cmd: str) -> str:
        result = subprocess.run(["git", *cmd], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(cmd)} failed: {result.stderr.strip()}")
        return result.stdout

    top = Path(git("rev-parse", "--show-toplevel").strip())
    # Three dots: compare against the merge base, i.e. what this branch changed.
    changed = {
        (top / line).resolve()
        for line in git("diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD").splitlines()
        if line.strip()
    }
    return [f for f in files if f.resolve() in changed]


def _use_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ
