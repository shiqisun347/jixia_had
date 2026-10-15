"""Read-only migration checks shared by release preflight and its regression tests."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def current_migration_head(repo_root: Path) -> str:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise ValueError("checkout has no migration head")
    return head


def database_at_migration_head(revisions: list[str], expected_head: str) -> bool:
    # Alembic stores one row per branch. Multiple rows must fail closed rather
    # than allowing a scalar query to accept only the first matching revision.
    return revisions == [expected_head]
