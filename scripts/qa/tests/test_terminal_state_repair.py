from scripts.ops.repair_terminal_state import can_apply


def test_terminal_repair_gate_requires_no_live_rooms_or_matches() -> None:
    assert can_apply({"nonterminal_rooms": 0, "nonterminal_matches": 0})
    assert not can_apply({"nonterminal_rooms": 1, "nonterminal_matches": 0})
    assert not can_apply({"nonterminal_rooms": 0, "nonterminal_matches": 1})


def test_terminal_repair_gate_is_fail_closed_for_missing_fields() -> None:
    assert not can_apply({})
