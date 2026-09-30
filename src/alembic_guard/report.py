"""Output formats: human-readable text, GitHub Actions annotations and JSON."""

from __future__ import annotations

import json
from collections.abc import Sequence

from .checker import Finding
from .rules import Severity

_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def format_text(findings: Sequence[Finding], files_checked: int, color: bool = False) -> str:
    def paint(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if color else text

    lines: list[str] = []
    for f in findings:
        sev_color = _RED if f.severity is Severity.ERROR else _YELLOW
        location = paint(f"{f.path}:{f.line}:{f.col}", _BOLD)
        tag = paint(f"{f.rule_id} {f.severity.value}", sev_color)
        lines.append(f"{location}  {tag}  {f.message}")
        lines.append(paint(f"    fix: {f.rule.fix}", _DIM))

    lines.append(_summary(findings, files_checked))
    return "\n".join(lines)


def format_github(findings: Sequence[Finding]) -> str:
    """Workflow commands that GitHub renders as inline annotations on the PR diff."""
    lines = []
    for f in findings:
        level = "error" if f.severity is Severity.ERROR else "warning"
        title = f"{f.rule_id} {f.rule.name}"
        body = f"{f.message} Fix: {f.rule.fix}"
        lines.append(
            f"::{level} file={_escape_prop(f.path)},line={f.line},col={f.col},"
            f"title={_escape_prop(title)}::{_escape_data(body)}"
        )
    return "\n".join(lines)


def format_json(findings: Sequence[Finding]) -> str:
    return json.dumps(
        [
            {
                "path": f.path,
                "line": f.line,
                "col": f.col,
                "rule": f.rule_id,
                "name": f.rule.name,
                "severity": f.severity.value,
                "message": f.message,
                "fix": f.rule.fix,
            }
            for f in findings
        ],
        indent=2,
    )


def _summary(findings: Sequence[Finding], files_checked: int) -> str:
    files = f"{files_checked} migration{'s' if files_checked != 1 else ''}"
    if not findings:
        return f"All clear: checked {files}, no risky operations found."
    errors = sum(f.severity is Severity.ERROR for f in findings)
    warnings = len(findings) - errors
    return (
        f"Found {errors} error{'s' if errors != 1 else ''} and "
        f"{warnings} warning{'s' if warnings != 1 else ''} in {files}."
    )


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_prop(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")
