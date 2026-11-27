"""Tests for when the can watcher looks, asks, trusts and nags. Plain python3."""

from __future__ import annotations

from datetime import datetime, time
import importlib.util
import os
import sys

_PATH = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "chores_discord", "trash.py"
)
_spec = importlib.util.spec_from_file_location("cd_trash", _PATH)
trash = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = trash
_spec.loader.exec_module(trash)

H = trash.HOUR
NOW = 1_800_000_000.0


def test_model_status_gates() -> None:
    s = trash.model_status({}, None, 5, 0.9)
    assert not s.ready and s.phase == trash.PHASE_EMPTY
    s = trash.model_status({"out": 5, "in": 4}, 1.0, 5, 0.9)
    assert not s.ready and s.phase == trash.PHASE_LEARNING  # too few "in"
    s = trash.model_status({"out": 5, "in": 5}, 0.85, 5, 0.9)
    assert not s.ready  # not accurate enough
    s = trash.model_status({"out": 5, "in": 9}, 0.93, 5, 0.9)
    assert s.ready and s.phase == trash.PHASE_READY
    assert "5 out" in s.summary() and "93%" in s.summary()


def test_verdict() -> None:
    assert trash.verdict(None, 1.0, 0.75) == trash.UNSURE
    assert trash.verdict("out", 0.7, 0.75) == trash.UNSURE
    assert trash.verdict("out", 0.8, 0.75) == trash.OUT
    assert trash.verdict("in", 0.9, 0.75) == trash.NOT_OUT


def test_daylight() -> None:
    assert trash.is_daylight(20, 120, 3, 35)
    assert not trash.is_daylight(1, 120, 3, 35)  # sun too low
    assert not trash.is_daylight(20, 20, 3, 35)  # crop too dark
    assert trash.is_daylight(None, 120, 3, 35)  # no sun.sun: brightness decides
    assert not trash.is_daylight(None, None, 3, 35)


def test_parse_time() -> None:
    assert trash.parse_time("19:30:00", time(1)) == time(19, 30)
    assert trash.parse_time("bad", time(1)) == time(1)


def test_cans_night() -> None:
    tuesday = datetime(2026, 12, 1, 19, 0)
    assert trash.is_cans_night(tuesday, [1])
    assert not trash.is_cans_night(tuesday, [3])
    assert trash.night_key(tuesday) == "2026-12-01"


def _ask(**kw):
    base = dict(now=NOW, result=trash.UNSURE, ready=False, asks=[], max_per_day=3,
                min_gap_hours=2, cans_night=False)
    base.update(kw)
    return trash.should_ask(**base)


def test_should_ask_budget() -> None:
    assert _ask()
    assert not _ask(result=trash.DARK)
    assert not _ask(asks=[NOW - 1 * H])  # gap
    assert _ask(asks=[NOW - 3 * H])
    assert not _ask(asks=[NOW - 3 * H, NOW - 6 * H, NOW - 9 * H])  # daily cap
    assert _ask(asks=[NOW - 3 * H, NOW - 6 * H, NOW - 9 * H], cans_night=True)  # +1
    assert _ask(asks=[NOW - 25 * H] * 5)  # yesterday's don't count


def test_ready_model_asks_only_when_unsure() -> None:
    assert not _ask(ready=True, result=trash.OUT)
    assert not _ask(ready=True, result=trash.NOT_OUT)
    assert _ask(ready=True, result=trash.UNSURE)


def test_plan_nag() -> None:
    fresh = NOW - 600
    assert trash.plan_nag(ready=True, last_result=trash.OUT, last_at=fresh, now=NOW).kind == "none"
    assert trash.plan_nag(ready=True, last_result=trash.NOT_OUT, last_at=fresh, now=NOW).kind == "nag"
    assert trash.plan_nag(ready=True, last_result=trash.UNSURE, last_at=fresh, now=NOW).kind == "ask"
    assert trash.plan_nag(ready=True, last_result=trash.DARK, last_at=fresh, now=NOW).kind == "ask"
    stale = NOW - 13 * H
    assert trash.plan_nag(ready=True, last_result=trash.OUT, last_at=stale, now=NOW).kind == "ask"
    assert trash.plan_nag(ready=False, last_result=trash.OUT, last_at=fresh, now=NOW).kind == "ask"


def test_reference_cap_per_label() -> None:
    refs: list = []
    for i in range(trash.REFS_PER_LABEL + 10):
        refs = trash.add_reference(refs, {"label": "in", "n": i})
    refs = trash.add_reference(refs, {"label": "out", "n": -1})
    counts = trash.label_counts(refs)
    assert counts == {"out": 1, "in": trash.REFS_PER_LABEL}
    assert refs[0]["n"] == 10  # oldest dropped


def test_prune_pending() -> None:
    pending = {f"s{i}": {"at": NOW - i * 3600} for i in range(60)}
    pending["old"] = {"at": NOW - trash.PENDING_TTL - 1}
    kept = trash.prune_pending(pending, NOW)
    assert "old" not in kept and len(kept) == trash.PENDING_MAX
    assert "s0" in kept and "s59" not in kept


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
