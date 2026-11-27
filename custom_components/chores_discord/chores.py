"""The chore catalogue and the pure rules for when a chore is due.

No Home Assistant or Discord imports, so the tests can load this file on its
own. Times are epoch seconds; the coordinator passes ``now`` in.

Adding a chore
--------------
Either add a line to ``DEFAULT_CHORES`` below, or (no code change) type it into
the integration's **Configure → Custom chores** box, one per line::

    Name | emoji | every N days | optional note

For example ``Clean litter box | 🐈 | 2``. Only the name is required.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

DAY = 86400.0

# A claim ("I'll take it") lapses after this long, so a forgotten claim
# doesn't hide a chore forever.
CLAIM_TTL = DAY

# Discord allows 25 options in a select menu; the board has one select.
MAX_CHORES = 25

# Undo history kept per chore (only the latest entry can be undone).
HISTORY_MAX = 5

# A quick report ("Trash needs taking out") lapses after this long if nobody
# marks the chore done.
REPORT_TTL = 2 * DAY

# Quick-report buttons on the board: Discord fits five buttons in a row.
MAX_QUICK = 5
DEFAULT_QUICK = ("trash", "recycling", "dishes", "counters", "bathroom")


@dataclass(frozen=True)
class Chore:
    """One chore definition. ``every_days`` 0 means "whenever needed"."""

    key: str
    name: str
    emoji: str = "🧽"
    every_days: float = 7
    note: str = ""


DEFAULT_CHORES: tuple[Chore, ...] = (
    Chore("dishes", "Dishes", "🍽️", 1, "Load or unload the dishwasher, or hand-wash"),
    Chore("trash", "Trash", "🗑️", 3, "Kitchen trash out; cans to the curb on trash night"),
    Chore("recycling", "Recycling", "♻️", 7, "Recycling out; bin to the curb on recycling night"),
    Chore("trash_ext", "Trash-Ext", "📦", 7, "Boxes and anything bulky"),
    Chore("vacuuming", "Vacuuming", "🧹", 7),
    Chore("mopping", "Mopping", "🪣", 14),
    Chore("bathroom", "Bathroom", "🚽", 7),
    Chore("counters", "Wipe counters", "✨", 2),
    Chore("fridge", "Clean out fridge", "🧊", 14),
)

# Status codes, in board order (most urgent first).
REPORTED = "reported"
OVERDUE = "overdue"
DUE = "due"
SOON = "soon"
FRESH = "fresh"
ANYTIME = "anytime"

STATUS_ICON = {
    REPORTED: "🔔",
    OVERDUE: "🔴",
    DUE: "🟠",
    SOON: "🟡",
    FRESH: "🟢",
    ANYTIME: "⚪",
}
_STATUS_RANK = {REPORTED: -1, OVERDUE: 0, DUE: 1, SOON: 2, ANYTIME: 3, FRESH: 4}


def slugify(name: str) -> str:
    """A stable key for a chore name: lowercase words joined by ``_``."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "chore"


def parse_custom(text: str) -> tuple[list[Chore], list[str]]:
    """Parse the custom-chores box. Returns (chores, problems).

    A bad line is reported and skipped rather than failing the whole box, so a
    typo can't make every chore disappear.
    """
    chores: list[Chore] = []
    problems: list[str] = []
    for number, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        name = parts[0]
        if not name:
            problems.append(f"line {number}: no name")
            continue
        emoji = parts[1] if len(parts) > 1 and parts[1] else "🧽"
        every = 7.0
        if len(parts) > 2 and parts[2]:
            match = re.search(r"\d+(?:\.\d+)?", parts[2])
            if match is None:
                problems.append(f"line {number}: '{parts[2]}' isn't a number of days")
                continue
            every = float(match.group())
        note = parts[3] if len(parts) > 3 else ""
        chores.append(Chore(slugify(name), name[:80], emoji[:8], every, note[:100]))
    return chores, problems


def catalogue(
    custom_text: str = "", disabled: list[str] | tuple[str, ...] = ()
) -> list[Chore]:
    """The active chore list: defaults (minus disabled) then custom chores.

    A custom chore with the same key as a default replaces it, so the person
    can change "Dishes" to every 2 days by typing ``Dishes | 🍽️ | 2``.
    Capped at ``MAX_CHORES`` (the Discord select limit).
    """
    custom, _ = parse_custom(custom_text)
    by_key: dict[str, Chore] = {}
    for chore in DEFAULT_CHORES:
        if chore.key not in disabled:
            by_key[chore.key] = chore
    for chore in custom:
        by_key[chore.key] = chore
    return list(by_key.values())[:MAX_CHORES]


# --- state -------------------------------------------------------------------
#
# State is a plain dict per chore key, so it round-trips through HA's Store:
#   {"last_done": float|None, "last_by": str|None, "last_by_id": int|None,
#    "claimed_by": str|None, "claimed_by_id": int|None, "claimed_at": float|None,
#    "history": [{"at", "by", "by_id"}...]}


def blank() -> dict:
    return {
        "last_done": None,
        "last_by": None,
        "last_by_id": None,
        "claimed_by": None,
        "claimed_by_id": None,
        "claimed_at": None,
        "reported_by": None,
        "reported_at": None,
        "history": [],
    }


def entry(state: dict, key: str) -> dict:
    """The state for one chore, created on first use."""
    current = state.get(key)
    if not isinstance(current, dict):
        current = blank()
        state[key] = current
    for field, value in blank().items():
        current.setdefault(field, value)
    return current


def mark_done(state: dict, key: str, now: float, who: str, who_id: int | None) -> dict:
    """Record a chore as done. Clears any claim. Returns the chore's entry."""
    item = entry(state, key)
    item["history"] = (
        item["history"]
        + [{"at": item["last_done"], "by": item["last_by"], "by_id": item["last_by_id"]}]
    )[-HISTORY_MAX:]
    item["last_done"] = now
    item["last_by"] = who
    item["last_by_id"] = who_id
    item["claimed_by"] = item["claimed_by_id"] = item["claimed_at"] = None
    item["reported_by"] = item["reported_at"] = None
    return item


def report(state: dict, key: str, now: float, who: str) -> bool:
    """Flag a chore as needing doing now. Returns False if already flagged."""
    item = entry(state, key)
    if reporter(item, now) is not None:
        return False
    item["reported_by"] = who
    item["reported_at"] = now
    return True


def reporter(item: dict, now: float) -> str | None:
    """Who flagged this chore as needing doing, if the flag is still live."""
    at = item.get("reported_at")
    if not item.get("reported_by") or at is None or now - at > REPORT_TTL:
        return None
    return item["reported_by"]


def short_name(name: str) -> str:
    """One word for a quick-report button: "Wipe counters" → "Counters"."""
    words = name.split()
    return name if len(words) <= 1 else words[-1].capitalize()


def quick_chores(chores: list[Chore], keys) -> list[Chore]:
    """The chores to show as quick-report buttons, in the configured order."""
    by_key = {c.key: c for c in chores}
    picked = [by_key[k] for k in keys if k in by_key]
    return picked[:MAX_QUICK]


def undo(state: dict, key: str, who_id: int | None) -> bool:
    """Undo the latest "done", only for the person who marked it."""
    item = entry(state, key)
    if item["last_done"] is None or not item["history"]:
        return False
    if who_id is not None and item["last_by_id"] not in (None, who_id):
        return False
    previous = item["history"].pop()
    item["last_done"] = previous["at"]
    item["last_by"] = previous["by"]
    item["last_by_id"] = previous["by_id"]
    return True


def toggle_claim(
    state: dict, key: str, now: float, who: str, who_id: int | None
) -> bool:
    """Claim a chore, or drop your own claim. Returns True if now claimed.

    Claiming over someone else's live claim takes it over: the board names the
    new claimant, and people sort it out in the room.
    """
    item = entry(state, key)
    if claimant(item, now) is not None and item["claimed_by_id"] == who_id:
        item["claimed_by"] = item["claimed_by_id"] = item["claimed_at"] = None
        return False
    item["claimed_by"] = who
    item["claimed_by_id"] = who_id
    item["claimed_at"] = now
    return True


def claimant(item: dict, now: float) -> str | None:
    """Who has claimed this chore, if the claim is still live."""
    at = item.get("claimed_at")
    if not item.get("claimed_by") or at is None or now - at > CLAIM_TTL:
        return None
    return item["claimed_by"]


def status(chore: Chore, item: dict, now: float) -> tuple[str, float | None]:
    """(status code, seconds until due; negative when overdue; None if n/a)."""
    if reporter(item, now) is not None:
        return REPORTED, 0.0
    if chore.every_days <= 0:
        return ANYTIME, None
    last = item.get("last_done")
    if last is None:
        return DUE, 0.0
    interval = chore.every_days * DAY
    left = last + interval - now
    if left < -min(DAY, interval * 0.5):
        return OVERDUE, left
    if left <= 0:
        return DUE, left
    if left <= min(DAY, interval * 0.25):
        return SOON, left
    return FRESH, left


def ago(seconds: float) -> str:
    """Short human span: ``just now``, ``3h``, ``2d``."""
    seconds = abs(seconds)
    if seconds < 3600:
        return "just now" if seconds < 600 else f"{int(seconds // 60)}m"
    if seconds < DAY:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // DAY)}d"


def line(chore: Chore, item: dict, now: float) -> str:
    """One board line: icon, name, then what matters about it right now."""
    code, left = status(chore, item, now)
    parts = [f"{STATUS_ICON[code]} {chore.emoji} **{chore.name}**"]
    who_reported = reporter(item, now)
    if code == REPORTED and who_reported:
        parts.append(f"{who_reported} says it needs doing ({ago(now - item['reported_at'])} ago)".replace("(just now ago)", "(just now)"))
    elif code == OVERDUE and left is not None:
        parts.append(f"overdue {ago(left)}")
    elif code == DUE:
        parts.append("due" if item["last_done"] is not None else "not done yet")
    elif code == SOON and left is not None:
        parts.append(f"due in {ago(left)}")
    if item["last_done"] is not None and code != REPORTED:
        by = f" by {item['last_by']}" if item["last_by"] else ""
        span = ago(now - item["last_done"])
        parts.append(f"done just now{by}" if span == "just now" else f"done {span} ago{by}")
    who = claimant(item, now)
    if who:
        parts.append(f"🙋 {who}")
    return " · ".join(parts)


def _sorted(chores: list[Chore], state: dict, now: float) -> list[tuple[str, Chore]]:
    rows = []
    for index, chore in enumerate(chores):
        code, left = status(chore, entry(state, chore.key), now)
        rows.append((_STATUS_RANK[code], left if left is not None else 0, index, code, chore))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [(row[3], row[4]) for row in rows]


def board_lines(chores: list[Chore], state: dict, now: float) -> list[str]:
    """One line per chore, most urgent first."""
    return [line(c, entry(state, c.key), now) for _, c in _sorted(chores, state, now)]


SECTION_NEEDS = "Needs doing"
SECTION_CLAIMED = "Claimed"
SECTION_SOON = "Coming up"
SECTION_DONE = "Done recently"


def board_sections(chores: list[Chore], state: dict, now: float) -> list[tuple[str, list[str]]]:
    """The board grouped for reading at a glance; empty groups are left out.

    A claimed chore sits under "Claimed" whatever its due state, since
    somebody has it in hand.
    """
    groups: dict[str, list[str]] = {
        SECTION_NEEDS: [], SECTION_CLAIMED: [], SECTION_SOON: [], SECTION_DONE: []
    }
    for code, chore in _sorted(chores, state, now):
        item = entry(state, chore.key)
        text = line(chore, item, now)
        if claimant(item, now):
            groups[SECTION_CLAIMED].append(text)
        elif code in (REPORTED, OVERDUE, DUE):
            groups[SECTION_NEEDS].append(text)
        elif code == SOON:
            groups[SECTION_SOON].append(text)
        else:
            groups[SECTION_DONE].append(text)
    return [(name, rows) for name, rows in groups.items() if rows]


def worst(chores: list[Chore], state: dict, now: float) -> str:
    """The most urgent status on the board (sets the card's colour)."""
    ordered = _sorted(chores, state, now)
    return ordered[0][0] if ordered else FRESH


def needing_attention(chores: list[Chore], state: dict, now: float) -> list[Chore]:
    """Chores that are due or overdue and nobody has claimed."""
    out = []
    for chore in chores:
        item = entry(state, chore.key)
        code, _ = status(chore, item, now)
        if code in (REPORTED, OVERDUE, DUE) and claimant(item, now) is None:
            out.append(chore)
    return out


def overdue(chores: list[Chore], state: dict, now: float) -> list[Chore]:
    return [c for c in chores if status(c, entry(state, c.key), now)[0] == OVERDUE]
