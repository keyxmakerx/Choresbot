"""Pure rules for the trash-can watcher: when to look, ask, trust and nag.

No Home Assistant or Discord imports. Times are epoch seconds plus a local
``datetime`` where the weekday or clock matters. The design rules these
functions enforce are in ``docs/design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import secrets

# Same values as vision.LABEL_*; repeated so this file loads on its own.
LABEL_OUT = "out"
LABEL_IN = "in"
LABELS = (LABEL_OUT, LABEL_IN)

# The bins the camera learns, each with its own references and nights. They
# share one region (they stand together at the curb) but are judged apart:
# "recycling out" is learned from the recycling answers only.
BIN_TRASH = "trash"
BIN_RECYCLING = "recycling"
BINS = (BIN_TRASH, BIN_RECYCLING)
BIN_NAME = {BIN_TRASH: "Trash", BIN_RECYCLING: "Recycling"}
BIN_EMOJI = {BIN_TRASH: "🗑️", BIN_RECYCLING: "♻️"}

# Which weeks a bin goes out (recycling is often every other week).
WEEKS_EVERY = "every"
WEEKS_EVEN = "even"
WEEKS_ODD = "odd"

HOUR = 3600.0
DAY = 86400.0

# Verdicts for one check.
OUT = "out"
NOT_OUT = "not_out"
UNSURE = "unsure"
DARK = "dark"
UNKNOWN = "unknown"

# Model phases, as shown to people.
PHASE_EMPTY = "no labels yet"
PHASE_LEARNING = "learning"
PHASE_READY = "ready"

# Caps so storage can't grow without bound.
REFS_PER_LABEL = 150
PENDING_MAX = 40
PENDING_TTL = 7 * DAY


@dataclass(frozen=True)
class ModelStatus:
    out: int
    not_out: int
    accuracy: float | None
    ready: bool

    @property
    def phase(self) -> str:
        if self.ready:
            return PHASE_READY
        if self.out + self.not_out == 0:
            return PHASE_EMPTY
        return PHASE_LEARNING

    def summary(self) -> str:
        acc = "—" if self.accuracy is None else f"{round(self.accuracy * 100)}%"
        return (
            f"{self.out} out · {self.not_out} not out · "
            f"self-test {acc} · {self.phase}"
        )


def model_status(
    counts: dict[str, int], accuracy: float | None, min_per_label: int, min_accuracy: float
) -> ModelStatus:
    """Ready means: enough of each label *and* it predicts its own labels well."""
    out = counts.get(LABEL_OUT, 0)
    not_out = counts.get(LABEL_IN, 0)
    ready = (
        out >= min_per_label
        and not_out >= min_per_label
        and accuracy is not None
        and accuracy >= min_accuracy
    )
    return ModelStatus(out, not_out, accuracy, ready)


def verdict(label: str | None, confidence: float, threshold: float) -> str:
    """Turn a prediction into OUT / NOT_OUT / UNSURE."""
    if label is None or confidence < threshold:
        return UNSURE
    return OUT if label == LABEL_OUT else NOT_OUT


def is_daylight(
    sun_elevation: float | None, brightness: float | None, min_elevation: float, min_brightness: float
) -> bool:
    """Look only when the sun is up enough and the crop isn't too dark.

    Either signal may be missing (no ``sun.sun``; not yet decoded); a missing
    one doesn't veto, but at least one must be present.
    """
    if sun_elevation is None and brightness is None:
        return False
    if sun_elevation is not None and sun_elevation < min_elevation:
        return False
    if brightness is not None and brightness < min_brightness:
        return False
    return True


def parse_time(value: str | time | None, fallback: time) -> time:
    if isinstance(value, time):
        return value
    try:
        parts = [int(p) for p in str(value).split(":")[:2]]
        return time(parts[0], parts[1] if len(parts) > 1 else 0)
    except (TypeError, ValueError, IndexError):
        return fallback


def is_cans_night(
    local: datetime, nights: list[int] | tuple[int, ...], weeks: str = WEEKS_EVERY
) -> bool:
    """Whether today (local) is a night this bin should go out.

    ``weeks`` even/odd uses the ISO week number, which the person can check
    against the collection calendar once and then forget.
    """
    if local.weekday() not in set(nights):
        return False
    if weeks == WEEKS_EVERY:
        return True
    week = local.isocalendar().week
    return (week % 2 == 0) == (weeks == WEEKS_EVEN)


def ref_bin(ref: dict) -> str:
    """A reference's bin; references from before recycling existed are trash."""
    return ref.get("bin") or BIN_TRASH


def night_key(local: datetime) -> str:
    return local.date().isoformat()


def should_ask(
    *,
    now: float,
    result: str,
    ready: bool,
    asks: list[float],
    max_per_day: int,
    min_gap_hours: float,
    cans_night: bool,
) -> bool:
    """Whether to post this snapshot asking for a label.

    While learning: ask (within the budget) so the reference set grows. Once
    ready: ask only when the model is unsure. A cans night gets one extra ask
    past the daily cap, because that is when "out" examples exist at all.
    """
    if result == DARK:
        return False
    if ready and result != UNSURE:
        return False
    recent = [t for t in asks if now - t < DAY]
    if recent and now - max(recent) < min_gap_hours * HOUR:
        return False
    cap = max_per_day + (1 if cans_night else 0)
    return len(recent) < cap


@dataclass(frozen=True)
class NagPlan:
    """What to post at nag time."""

    kind: str  # "none", "nag", "ask"
    reason: str


# A bin seen out stays out, so that sighting counts for the whole evening. A
# bin seen *not* out only says something about right now: in winter the last
# daylight look can be hours before the reminder, and the bins often go out
# after dark. So "not out" only drives a firm nag when it's this recent.
OUT_FRESH = 12 * HOUR
NOT_OUT_FRESH = 1 * HOUR


def plan_nag(*, ready: bool, last_result: str | None, last_at: float | None, now: float) -> NagPlan:
    """Decide what happens at the cans-night reminder time.

    ``last_result``/``last_at`` are from the last *daylight* look (dark looks
    never judge). A confident "out" in the last 12 hours keeps the bot quiet.
    A confident "not out" from the last hour is a plain nag. Anything less
    certain (still learning, unsure, or an older "not out" because it got
    dark since) is a question with buttons, which reminds just the same and
    doubles as a label when the image is usable.
    """
    age = None if last_at is None else now - last_at
    if ready and age is not None and age < OUT_FRESH and last_result == OUT:
        return NagPlan("none", "cans seen out")
    if ready and age is not None and age < NOT_OUT_FRESH and last_result == NOT_OUT:
        return NagPlan("nag", "cans not seen out")
    if ready and age is not None and age < OUT_FRESH and last_result == NOT_OUT:
        return NagPlan("ask", "not out at the last daylight look")
    if not ready:
        return NagPlan("ask", "still learning")
    return NagPlan("ask", "not sure")


# --- reference set ------------------------------------------------------------


def new_sample_id() -> str:
    return secrets.token_hex(6)


def add_reference(refs: list[dict], ref: dict) -> list[dict]:
    """Append a labelled reference, keeping the newest per bin and label."""
    refs = refs + [ref]
    kept: list[dict] = []
    seen: dict[tuple[str, str], int] = {}
    for item in reversed(refs):
        label = item.get("label")
        if label in LABELS:
            key = (ref_bin(item), label)
            if seen.get(key, 0) >= REFS_PER_LABEL:
                continue
            seen[key] = seen.get(key, 0) + 1
        kept.append(item)
    kept.reverse()
    return kept


def prune_pending(pending: dict[str, dict], now: float) -> dict[str, dict]:
    """Drop expired samples and keep the newest ``PENDING_MAX``."""
    fresh = {
        sid: item for sid, item in pending.items() if now - item.get("at", 0) < PENDING_TTL
    }
    newest = sorted(fresh.items(), key=lambda kv: kv[1].get("at", 0))[-PENDING_MAX:]
    return dict(newest)


def label_counts(refs: list[dict], bin_name: str | None = None) -> dict[str, int]:
    """Counts per label, for one bin when ``bin_name`` is given."""
    picked = [r for r in refs if bin_name is None or ref_bin(r) == bin_name]
    return {label: sum(1 for r in picked if r.get("label") == label) for label in LABELS}


def for_bin(refs: list[dict], bin_name: str) -> list[dict]:
    return [r for r in refs if ref_bin(r) == bin_name]
