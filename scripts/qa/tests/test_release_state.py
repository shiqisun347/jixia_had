import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from scripts.ops.migration_preflight import current_migration_head, database_at_migration_head
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

    assert current_migration_head(PROJECT_ROOT) == head
    assert "current_migration_head(Path.cwd())" in source
    assert "readonly EXPECTED_REVISION=" not in source


def test_server_preflight_blocks_nonterminal_rooms_as_well_as_matches() -> None:
    source = (PROJECT_ROOT / "scripts/ops/preflight_v2_1.sh").read_text(encoding="utf-8")

    assert "nonterminal_rooms" in source
    assert "nonterminal_room_statuses" in source
    assert "non-terminal rooms exist; do not deploy" in source


def _run_database_check(
    tmp_path: Path, payload: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    """Replace only the query subprocess, then execute the real validators."""

    wrapper = tmp_path / "preflight-python"
    calls = tmp_path / "python-calls.jsonl"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import json\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"calls = Path({str(calls)!r})\n"
        "source = sys.stdin.read()\n"
        'if "from jx_core.config import load_settings" in source:\n'
        '    assert "asyncio.run(main())" in source\n'
        '    with calls.open("a", encoding="utf-8") as handle:\n'
        '        handle.write(json.dumps("query_stubbed") + "\\n")\n'
        f"    print({json.dumps(payload)!r})\n"
        'elif "from scripts.ops.migration_preflight import database_at_migration_head" in source:\n'
        '    with calls.open("a", encoding="utf-8") as handle:\n'
        '        handle.write(json.dumps("validation_delegated") + "\\n")\n'
        '    result = subprocess.run([sys.executable, "-"], input=source, text=True)\n'
        "    raise SystemExit(result.returncode)\n"
        "else:\n"
        '    raise SystemExit("unexpected preflight Python invocation")\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; check_database; exit "$failures"',
            "bash",
            str(PROJECT_ROOT / "scripts/ops/preflight_v2_1.sh"),
        ],
        env={"PATH": os.defpath, "JX_PREFLIGHT_PYTHON": str(wrapper)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert calls.read_text(encoding="utf-8").splitlines() == [
        json.dumps("query_stubbed"),
        json.dumps("validation_delegated"),
    ]
    return result


def _clean_database_payload() -> dict[str, object]:
    head = current_migration_head(PROJECT_ROOT)
    return {
        "expected_revision": head,
        "revisions": [head],
        "active_matches": 0,
        "active_statuses": {},
        "nonterminal_rooms": 0,
        "nonterminal_room_statuses": {},
        "terminal_rooms_missing_end_at": 0,
        "terminal_room_leases": 0,
        "active_leases": 0,
    }


def test_database_preflight_accepts_zero_nonterminal_rooms(tmp_path: Path) -> None:
    result = _run_database_check(tmp_path, _clean_database_payload())

    assert result.returncode == 0
    assert "PASS  no non-terminal rooms" in result.stdout
    assert "PASS  database migration matches the checkout head" in result.stdout
    assert "PASS  no non-terminal matches" in result.stdout
    assert "PASS  terminal room timestamps and leases are reconciled" in result.stdout
    assert "PASS  no active room connection leases remain" in result.stdout
    assert result.stderr == ""


def test_database_preflight_blocks_rooms_when_there_are_no_active_matches(tmp_path: Path) -> None:
    payload = _clean_database_payload()
    payload["nonterminal_rooms"] = 3
    payload["nonterminal_room_statuses"] = {"WAITING": 3}

    result = _run_database_check(tmp_path, payload)

    assert result.returncode == 1
    assert "active_matches=0 active_statuses=none" in result.stdout
    assert "PASS  no non-terminal matches" in result.stdout
    assert "nonterminal_rooms=3 nonterminal_room_statuses=WAITING=3" in result.stdout
    assert "FAIL  non-terminal rooms exist; do not deploy" in result.stderr


@pytest.mark.parametrize("room_count", ["0", None, False])
def test_database_preflight_rejects_malformed_room_counts(
    tmp_path: Path, room_count: object
) -> None:
    payload = _clean_database_payload()
    payload["nonterminal_rooms"] = room_count

    result = _run_database_check(tmp_path, payload)

    assert result.returncode == 1
    assert "PASS  no non-terminal matches" in result.stdout
    assert "FAIL  non-terminal rooms exist; do not deploy" in result.stderr
    assert "PASS  no non-terminal rooms" not in result.stdout


def test_database_preflight_rejects_a_missing_room_count(tmp_path: Path) -> None:
    payload = _clean_database_payload()
    del payload["nonterminal_rooms"]

    result = _run_database_check(tmp_path, payload)

    assert result.returncode == 1
    assert "PASS  no non-terminal matches" in result.stdout
    assert "FAIL  non-terminal rooms exist; do not deploy" in result.stderr
    assert "PASS  no non-terminal rooms" not in result.stdout


@pytest.mark.parametrize(
    ("revisions", "accepted"),
    [
        (["current_head"], True),
        ([], False),
        (["previous_head"], False),
        (["current_head_suffix"], False),
        (["current_head", "other_branch"], False),
        (["other_branch", "current_head"], False),
    ],
)
def test_database_migration_check_requires_exactly_the_checkout_head(
    revisions: list[str], accepted: bool
) -> None:
    assert database_at_migration_head(revisions, "current_head") is accepted


def test_migration_check_rejects_a_checkout_with_multiple_heads(tmp_path: Path) -> None:
    migration_root = tmp_path / "migrations"
    versions = migration_root / "versions"
    versions.mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nscript_location = %(here)s/migrations\n", encoding="utf-8"
    )
    for revision in ("first", "second"):
        (versions / f"{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = None\n", encoding="utf-8"
        )

    with pytest.raises(CommandError, match="multiple heads"):
        current_migration_head(tmp_path)
