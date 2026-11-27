"""Personal settings: how each person wants to hear from the bot.

Pure Python (no Home Assistant, no Discord) so it can be tested standalone.
Everyone starts on the defaults; a person only gets a stored entry once they
change something in their private 👤 Mine panel.
"""

from __future__ import annotations

# Bin night reminders.
BIN_CLAIMED = "claimed"  # ping me only if I claimed the bin's chore (the default)
BIN_PING = "ping"  # always mention me on the channel card
BIN_DM = "dm"  # send me the reminder privately instead
BIN_OFF = "off"  # never ping me, even if I claimed it

OFF = "off"
DM = "dm"
CLAIMED = "claimed"

DEFAULTS: dict[str, str] = {"bin": BIN_CLAIMED, "summary": OFF, "flags": OFF, "quiet": OFF}

# setting -> (heading, [(value, label, emoji)])
CHOICES: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "bin": (
        "Bin night reminders",
        [
            (BIN_CLAIMED, "Ping me only if I claimed it", "🙋"),
            (BIN_PING, "Always ping me in the channel", "🔔"),
            (BIN_DM, "Send me a DM instead", "✉️"),
            (BIN_OFF, "Never ping me", "🔕"),
        ],
    ),
    "summary": (
        "Daily summary",
        [
            (OFF, "No daily summary", "🔕"),
            (DM, "DM me what needs doing each morning", "✉️"),
        ],
    ),
    "flags": (
        "When someone flags a chore",
        [
            (OFF, "Don't tell me", "🔕"),
            (DM, "DM me about any chore", "✉️"),
            (CLAIMED, "DM me only for chores I claimed", "🙋"),
        ],
    ),
    "quiet": (
        "Quiet hours",
        [
            (OFF, "No quiet hours", "🌞"),
            ("21-7", "9 PM to 7 AM", "🌙"),
            ("22-7", "10 PM to 7 AM", "🌙"),
            ("22-8", "10 PM to 8 AM", "🌙"),
            ("23-8", "11 PM to 8 AM", "🌙"),
            ("0-9", "Midnight to 9 AM", "🌙"),
        ],
    ),
}

SETTINGS = tuple(CHOICES)


def prefs(people: dict, uid: int | None) -> dict[str, str]:
    """Someone's settings, defaults filled in. Unknown people get the defaults."""
    stored = people.get(str(uid), {}) if uid is not None else {}
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


def set_pref(people: dict, uid: int, name: str, setting: str, value: str) -> bool:
    """Change one setting. False for an unknown setting or value."""
    if setting not in CHOICES or value not in {v for v, _, _ in CHOICES[setting][1]}:
        return False
    entry = people.setdefault(str(uid), {})
    entry[setting] = value
    entry["name"] = name
    return True


def choice_label(setting: str, value: str) -> str:
    for v, label, _ in CHOICES[setting][1]:
        if v == value:
            return label
    return value


def in_quiet(quiet: str, hour: int) -> bool:
    """Whether ``hour`` (0-23, local) falls inside a "start-end" quiet window."""
    if not quiet or quiet == OFF:
        return False
    try:
        start, end = (int(x) for x in quiet.split("-"))
    except ValueError:
        return False
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def bin_recipients(
    people: dict, claimed_ids: set[int], hour: int
) -> tuple[list[int], list[tuple[int, bool]]]:
    """Who to mention on the bin-night card, and who to DM (with whether that
    DM should be silent because it's their quiet hours).

    Claimers are mentioned unless they turned it off or asked for a DM.
    Anyone on "always ping" is mentioned. Nobody is mentioned during their
    quiet hours; a DM then arrives silently instead.
    """
    mention: list[int] = []
    dm: list[tuple[int, bool]] = []
    candidates = {int(k) for k in people if str(k).isdigit()} | set(claimed_ids)
    for uid in sorted(candidates):
        p = prefs(people, uid)
        quiet = in_quiet(p["quiet"], hour)
        choice = p["bin"]
        if choice == BIN_DM:
            dm.append((uid, quiet))
        elif choice == BIN_PING or (choice == BIN_CLAIMED and uid in claimed_ids):
            if not quiet:
                mention.append(uid)
    return mention, dm


def subscribers(people: dict, setting: str, values: tuple[str, ...] = (DM,)) -> list[int]:
    """Everyone whose ``setting`` is one of ``values``."""
    return sorted(
        int(k) for k, v in people.items()
        if str(k).isdigit() and isinstance(v, dict) and v.get(setting, DEFAULTS[setting]) in values
    )
