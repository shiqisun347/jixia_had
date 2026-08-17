from scripts.qa.release_state import ACTIVE_MATCH_STATUSES


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
