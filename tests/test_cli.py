import json
import subprocess
from pathlib import Path

import pytest

from alembic_guard.cli import main

DANGEROUS = """\
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("users", sa.Column("email", sa.String(), nullable=False))
"""

RISKY = """\
from alembic import op


def upgrade():
    op.create_index("ix_users_email", "users", ["email"])
"""

SAFE = """\
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("users", sa.Column("bio", sa.Text()))
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True)
    (tmp_path / "migrations" / "env.py").write_text("")
    monkeypatch.chdir(tmp_path)
    return versions


def test_safe_migration_exits_zero(project, capsys):
    (project / "0001_safe.py").write_text(SAFE)
    assert main([]) == 0
    assert "All clear" in capsys.readouterr().out


def test_error_exits_one_and_autodiscovers(project, capsys):
    (project / "0001_bad.py").write_text(DANGEROUS)
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "AG001" in out and "0001_bad.py:6:5" in out


def test_warnings_pass_unless_strict(project):
    (project / "0001_index.py").write_text(RISKY)
    assert main([]) == 0
    assert main(["--strict"]) == 1


def test_ignore_flag_and_config(project):
    (project / "0001_bad.py").write_text(DANGEROUS)
    assert main(["--ignore", "ag001"]) == 0
    Path("pyproject.toml").write_text('[tool.alembic-guard]\nignore = ["AG001"]\n')
    assert main([]) == 0


def test_strict_from_config(project):
    (project / "0001_index.py").write_text(RISKY)
    Path("pyproject.toml").write_text("[tool.alembic-guard]\nstrict = true\n")
    assert main([]) == 1


def test_unknown_rule_id_is_a_usage_error(project):
    assert main(["--ignore", "AG999"]) == 2


def test_github_format(project, capsys):
    (project / "0001_bad.py").write_text(DANGEROUS)
    main(["--format", "github"])
    out = capsys.readouterr().out
    assert "::error file=migrations/versions/0001_bad.py,line=6,col=5,title=AG001 add-not-null-column::" in out


def test_json_format(project, capsys):
    (project / "0001_bad.py").write_text(DANGEROUS)
    main(["--format", "json"])
    [item] = json.loads(capsys.readouterr().out)
    assert item["rule"] == "AG001" and item["severity"] == "error"


def test_missing_path_is_a_usage_error(project):
    assert main(["does/not/exist"]) == 2


def test_no_migrations_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 2


def test_explain_and_list_rules(capsys):
    assert main(["--explain", "ag003"]) == 0
    assert "autocommit_block" in capsys.readouterr().out
    assert main(["--list-rules"]) == 0
    assert capsys.readouterr().out.count("\n") == 10


def test_diff_base_only_checks_changed_files(project):
    def git(*args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (project / "0001_old.py").write_text(DANGEROUS)
    git("add", ".")
    git("commit", "-m", "old")
    git("checkout", "-b", "feature")
    (project / "0002_new.py").write_text(SAFE)
    git("add", ".")
    git("commit", "-m", "new")

    assert main([]) == 1  # the old migration is dangerous
    assert main(["--diff-base", "main"]) == 0  # but this branch only added a safe one
