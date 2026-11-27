"""Personal settings: defaults, validation, quiet hours, who gets reminded."""

from __future__ import annotations

import importlib.util
import os
import sys

_PATH = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "chores_discord", "people.py"
)
_spec = importlib.util.spec_from_file_location("cd_people", _PATH)
people = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = people
_spec.loader.exec_module(people)


def test_defaults_for_strangers():
    assert people.prefs({}, 1) == people.DEFAULTS
    assert people.prefs({}, None) == people.DEFAULTS


def test_set_pref_validates():
    folks: dict = {}
    assert people.set_pref(folks, 7, "Sam", "bin", "dm")
    assert not people.set_pref(folks, 7, "Sam", "bin", "shout")
    assert not people.set_pref(folks, 7, "Sam", "colour", "red")
    assert people.prefs(folks, 7)["bin"] == "dm" and folks["7"]["name"] == "Sam"


def test_quiet_hours_wrap_midnight():
    assert people.in_quiet("22-7", 23) and people.in_quiet("22-7", 3)
    assert not people.in_quiet("22-7", 7) and not people.in_quiet("22-7", 21)
    assert people.in_quiet("0-9", 0) and not people.in_quiet("0-9", 9)
    assert not people.in_quiet("off", 3) and not people.in_quiet("bad", 3)


def test_bin_recipients():
    folks: dict = {}
    # Claimers are pinged by default.
    assert people.bin_recipients(folks, {1}, 19) == ([1], [])
    people.set_pref(folks, 1, "A", "bin", "off")
    people.set_pref(folks, 2, "B", "bin", "ping")
    people.set_pref(folks, 3, "C", "bin", "dm")
    mention, dm = people.bin_recipients(folks, {1}, 19)
    assert mention == [2] and dm == [(3, False)]
    # Quiet hours: no mention, and DMs go silently.
    people.set_pref(folks, 2, "B", "quiet", "21-7")
    people.set_pref(folks, 3, "C", "quiet", "21-7")
    mention, dm = people.bin_recipients(folks, set(), 22)
    assert mention == [] and dm == [(3, True)]


def test_subscribers():
    folks: dict = {}
    people.set_pref(folks, 5, "E", "summary", "dm")
    people.set_pref(folks, 6, "F", "flags", "claimed")
    assert people.subscribers(folks, "summary") == [5]
    assert people.subscribers(folks, "flags") == []
    assert people.subscribers(folks, "flags", ("claimed",)) == [6]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
