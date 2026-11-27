"""Tests for the chore catalogue and due rules. Runnable with plain python3."""

from __future__ import annotations

import importlib.util
import os
import sys

_PATH = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "chores_discord", "chores.py"
)
_spec = importlib.util.spec_from_file_location("cd_chores", _PATH)
chores = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = chores
_spec.loader.exec_module(chores)

DAY = chores.DAY
T0 = 1_800_000_000.0


def test_defaults_include_the_asked_for_chores() -> None:
    names = {c.name for c in chores.catalogue()}
    for wanted in ("Vacuuming", "Trash", "Trash-Ext", "Dishes", "Mopping"):
        assert wanted in names, wanted


def test_custom_lines() -> None:
    parsed, problems = chores.parse_custom(
        "Clean litter box | 🐈 | 2\n# comment\n\nWater plants|🪴|every 4 days|front room\nJust a name"
    )
    assert not problems
    assert [c.key for c in parsed] == ["clean_litter_box", "water_plants", "just_a_name"]
    assert parsed[1].every_days == 4 and parsed[1].note == "front room"
    assert parsed[2].emoji == "🧽" and parsed[2].every_days == 7


def test_bad_line_is_reported_not_fatal() -> None:
    parsed, problems = chores.parse_custom("Good | ✨ | 3\nBad | x | soon\n | 🧽")
    assert [c.name for c in parsed] == ["Good"]
    assert len(problems) == 2


def test_custom_replaces_default_and_disable() -> None:
    cat = chores.catalogue("Dishes | 🍽️ | 2", disabled=["mopping"])
    dishes = next(c for c in cat if c.key == "dishes")
    assert dishes.every_days == 2
    assert all(c.key != "mopping" for c in cat)


def test_cap_at_25() -> None:
    text = "\n".join(f"Chore {i}" for i in range(40))
    assert len(chores.catalogue(text)) == chores.MAX_CHORES


def test_status_progression() -> None:
    weekly = chores.Chore("v", "Vacuuming", "🧹", 7)
    state: dict = {}
    item = chores.entry(state, "v")
    assert chores.status(weekly, item, T0)[0] == chores.DUE  # never done
    chores.mark_done(state, "v", T0, "Sam", 1)
    assert chores.status(weekly, item, T0 + DAY)[0] == chores.FRESH
    assert chores.status(weekly, item, T0 + 6.5 * DAY)[0] == chores.SOON
    assert chores.status(weekly, item, T0 + 7.5 * DAY)[0] == chores.DUE
    assert chores.status(weekly, item, T0 + 9 * DAY)[0] == chores.OVERDUE
    anytime = chores.Chore("a", "Fridge", "🧊", 0)
    assert chores.status(anytime, chores.entry(state, "a"), T0)[0] == chores.ANYTIME


def test_claims_and_expiry() -> None:
    state: dict = {}
    assert chores.toggle_claim(state, "d", T0, "Sam", 1) is True
    item = chores.entry(state, "d")
    assert chores.claimant(item, T0 + 60) == "Sam"
    assert chores.toggle_claim(state, "d", T0 + 60, "Sam", 1) is False  # own claim drops
    assert chores.toggle_claim(state, "d", T0, "Sam", 1) is True
    assert chores.toggle_claim(state, "d", T0 + 5, "Alex", 2) is True  # takeover
    assert chores.claimant(item, T0 + 5) == "Alex"
    assert chores.claimant(item, T0 + chores.CLAIM_TTL + 10) is None
    chores.mark_done(state, "d", T0 + 10, "Alex", 2)
    assert chores.claimant(item, T0 + 11) is None


def test_undo_only_by_marker() -> None:
    state: dict = {}
    chores.mark_done(state, "d", T0, "Sam", 1)
    chores.mark_done(state, "d", T0 + 100, "Alex", 2)
    assert chores.undo(state, "d", 1) is False
    assert chores.undo(state, "d", 2) is True
    assert chores.entry(state, "d")["last_by"] == "Sam"
    assert chores.undo(state, "d", 1) is True
    assert chores.entry(state, "d")["last_done"] is None
    assert chores.undo(state, "d", 1) is False


def test_board_orders_by_urgency() -> None:
    cat = [
        chores.Chore("a", "Fresh one", "🟢", 7),
        chores.Chore("b", "Overdue one", "🔴", 1),
        chores.Chore("c", "Never done", "🟠", 3),
    ]
    state: dict = {}
    chores.mark_done(state, "a", T0, "Sam", 1)
    chores.mark_done(state, "b", T0 - 5 * DAY, "Alex", 2)
    lines = chores.board_lines(cat, state, T0 + 3600)
    assert "Overdue one" in lines[0] and "overdue" in lines[0]
    assert "Never done" in lines[1] and "not done yet" in lines[1]
    assert "Fresh one" in lines[2] and "by Sam" in lines[2]
    assert [c.key for c in chores.overdue(cat, state, T0 + 3600)] == ["b"]
    assert {c.key for c in chores.needing_attention(cat, state, T0 + 3600)} == {"b", "c"}


def test_quick_reports() -> None:
    state: dict = {}
    cat = chores.catalogue()
    assert [c.key for c in chores.quick_chores(cat, chores.DEFAULT_QUICK)] == list(chores.DEFAULT_QUICK)
    assert chores.short_name("Wipe counters") == "Counters" and chores.short_name("Trash") == "Trash"
    trash = next(c for c in cat if c.key == "trash")
    chores.mark_done(state, "trash", T0, "Alex", 2)
    assert chores.report(state, "trash", T0 + 60, "Sam") is True
    assert chores.report(state, "trash", T0 + 70, "Key") is False  # already flagged
    assert chores.status(trash, chores.entry(state, "trash"), T0 + 80)[0] == chores.REPORTED
    sections = dict(chores.board_sections(cat, state, T0 + 80))
    assert any("Sam says it needs doing" in line for line in sections["Needs doing"])
    assert chores.worst(cat, state, T0 + 80) == chores.REPORTED
    chores.mark_done(state, "trash", T0 + 90, "Sam", 1)
    assert chores.reporter(chores.entry(state, "trash"), T0 + 100) is None
    chores.report(state, "trash", T0, "Sam")
    assert chores.reporter(chores.entry(state, "trash"), T0 + chores.REPORT_TTL + 1) is None


def test_claimed_section() -> None:
    state: dict = {}
    cat = chores.catalogue()
    chores.toggle_claim(state, "dishes", T0, "Alex", 3)
    sections = dict(chores.board_sections(cat, state, T0 + 10))
    assert any("Dishes" in line for line in sections["Claimed"])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
