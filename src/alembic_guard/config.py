"""Read `[tool.alembic-guard]` from pyproject.toml."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class Config:
    paths: list[str] = field(default_factory=list)
    ignore: set[str] = field(default_factory=set)
    strict: bool = False
    include_downgrade: bool = False


def load_config(directory: Path) -> Config:
    pyproject = directory / "pyproject.toml"
    if not pyproject.is_file():
        return Config()
    with pyproject.open("rb") as fh:
        section = tomllib.load(fh).get("tool", {}).get("alembic-guard", {})
    return Config(
        paths=list(section.get("paths", [])),
        ignore={rule.upper() for rule in section.get("ignore", [])},
        strict=bool(section.get("strict", False)),
        include_downgrade=bool(section.get("include-downgrade", False)),
    )
