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


def is_cans_night(local: datetime, nights: list[int] | tuple[int, ...]) -> bool:
    """Whether today (local) is a night the cans should go out."""
    return local.weekday() in set(nights)


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


def plan_nag(*, ready: bool, last_result: str | None, last_at: float | None, now: float) -> NagPlan:
    """Decide what happens at the cans-night reminder time.

    Only a confident "out" from a check in the last 12 hours keeps the bot
    quiet. Confident "not out" is a plain nag. Anything less certain (still
    learning, unsure, dark, no recent check) is a question with buttons, which
    reminds just the same and doubles as a label when the image is usable.
    """
    fresh = last_at is not None and now - last_at < 12 * HOUR
    if ready and fresh and last_result == OUT:
        return NagPlan("none", "cans seen out")
    if ready and fresh and last_result == NOT_OUT:
        return NagPlan("nag", "cans not seen out")
    if not ready:
        return NagPlan("ask", "still learning")
    return NagPlan("ask", "not sure")


# --- reference set ------------------------------------------------------------


def new_sample_id() -> str:
    return secrets.token_hex(6)


def add_reference(refs: list[dict], ref: dict) -> list[dict]:
    """Append a labelled reference, keeping the newest per label up to the cap."""
    refs = refs + [ref]
    kept: list[dict] = []
    seen = {label: 0 for label in LABELS}
    for item in reversed(refs):
        label = item.get("label")
        if label in seen:
            if seen[label] >= REFS_PER_LABEL:
                continue
            seen[label] += 1
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


def label_counts(refs: list[dict]) -> dict[str, int]:
    return {label: sum(1 for r in refs if r.get("label") == label) for label in LABELS}
