import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from scripts.qa.release_state import ACTIVE_MATCH_STATUSES

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_release_audit_counts_every_nonterminal_match_status() -> None:
    assert set(ACTIVE_MATCH_STATUSES) == {
        "START_PENDING_RUNTIME",
        "START_COUNTDOWN",
        "RUNNING",
        "PAUSED",
        "SYSTEM_RECOVERY",
        "ERROR",
    }
    assert "RECOVERY_REQUIRED" not in ACTIVE_MATCH_STATUSES


def test_server_preflight_tracks_the_current_migration_head() -> None:
    alembic = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic.set_main_option("path_separator", "os")
    head = ScriptDirectory.from_config(alembic).get_current_head()
    source = (PROJECT_ROOT / "scripts/ops/preflight_v2_1.sh").read_text(encoding="utf-8")
    match = re.search(r'^readonly EXPECTED_REVISION="([^"]+)"$', source, re.MULTILINE)

    assert match is not None
    assert match.group(1) == head
