"""Catch Alembic migrations that lock tables or break running code."""

from .checker import Finding, check_file, check_source
from .rules import RULES, Rule, Severity

__version__ = "0.1.0"

__all__ = ["Finding", "RULES", "Rule", "Severity", "check_file", "check_source", "__version__"]
