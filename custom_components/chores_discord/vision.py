"""Fully local trash-can detection by comparing a cropped region to labels.

Pillow only (it ships with Home Assistant core), no numpy, OpenCV or network.
A snapshot is decoded at reduced size, the region of interest (where the cans
sit at the curb) is cropped, and three small descriptors are taken from the
crop:

* a hue/saturation colour histogram (cans are a distinct colour against the
  curb, and a histogram ignores exactly where in the box they stand);
* a 16x16 grey thumbnail, brightness-normalised so a cloud passing over
  doesn't look like a different scene;
* an 8x8 grid of edge strength (a can adds hard vertical edges).

Classification is k-nearest-neighbours against every labelled reference with
the same region. The references *are* the model, so each label you give in
Discord is learned immediately and can be inspected or removed. Leave-one-out
accuracy over the references says when there is enough data to trust it.

Whole pipeline on a 1080p JPEG: one reduced-size decode plus a few thousand
pixel reads, a few tens of milliseconds on a Home Assistant Green. All
functions are synchronous and pure; the coordinator runs ``analyse`` in the
executor.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import math

from PIL import Image, ImageDraw, ImageFilter, ImageOps

# Bump when the descriptor layout changes; references from another version are
# ignored rather than compared nonsensically.
FEATURE_VERSION = 1

LABEL_OUT = "out"
LABEL_IN = "in"
LABELS = (LABEL_OUT, LABEL_IN)

_HUE_BINS = 12
_SAT_BINS = 3
_VAL_BINS = 4
_THUMB = 16
_EDGE_GRID = 8
_HIST_SIZE = 64  # the crop is resized to this square before the histogram

# Relative weight of each descriptor in the combined distance.
_W_HIST = 0.4
_W_GRAY = 0.3
_W_EDGE = 0.3

# Decode no larger than this; the region is a small part of the frame anyway.
_DECODE_SIZE = (960, 540)


@dataclass(frozen=True)
class Analysis:
    """What one snapshot yields."""

    features: dict
    brightness: float  # mean grey of the crop, 0-255
    crop_jpeg: bytes  # the region, enlarged, for posting in Discord


@dataclass(frozen=True)
class Prediction:
    label: str | None  # None when there is nothing to compare against
    confidence: float  # share of the weighted vote for ``label``, 0-1
    nearest: float  # distance to the closest reference, 0 (same) to ~1
    compared: int  # how many references were used


def clamp_roi(roi: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Normalise (left, top, right, bottom) fractions to a valid box."""
    left, top, right, bottom = (min(1.0, max(0.0, float(v))) for v in roi)
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    # A degenerate box would crop to nothing; give it at least 2% each way.
    if right - left < 0.02:
        right = min(1.0, left + 0.02)
        left = right - 0.02
    if bottom - top < 0.02:
        bottom = min(1.0, top + 0.02)
        top = bottom - 0.02
    return left, top, right, bottom


def _open(image_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes))
    # JPEG draft mode decodes at 1/2, 1/4 or 1/8 scale: most of the speed win.
    try:
        img.draft("RGB", _DECODE_SIZE)
    except Exception:  # noqa: BLE001 - non-JPEG images just decode fully
        pass
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _box(img: Image.Image, roi) -> tuple[int, int, int, int]:
    left, top, right, bottom = clamp_roi(roi)
    width, height = img.size
    box = (
        int(left * width),
        int(top * height),
        max(int(left * width) + 1, int(round(right * width))),
        max(int(top * height) + 1, int(round(bottom * height))),
    )
    return box


def _normalise(values: list[float]) -> list[float]:
    """Zero mean, unit variance; a flat vector stays all zeros."""
    count = len(values)
    mean = sum(values) / count
    var = sum((v - mean) ** 2 for v in values) / count
    if var < 1e-9:
        return [0.0] * count
    std = math.sqrt(var)
    return [round((v - mean) / std, 4) for v in values]


def describe(crop: Image.Image) -> dict:
    """The three descriptors for an RGB crop."""
    small = crop.resize((_HIST_SIZE, _HIST_SIZE), Image.Resampling.BILINEAR)
    hsv = small.convert("HSV")
    hist = [0.0] * (_HUE_BINS * _SAT_BINS + _VAL_BINS)
    for h, s, v in hsv.getdata():
        sat_bin = min(_SAT_BINS - 1, s * _SAT_BINS // 256)
        if s < 40:
            # Near-grey pixels have no meaningful hue; count them by value.
            hist[_HUE_BINS * _SAT_BINS + min(_VAL_BINS - 1, v * _VAL_BINS // 256)] += 1
        else:
            hist[(h * _HUE_BINS // 256) * _SAT_BINS + sat_bin] += 1
    total = sum(hist) or 1.0
    hist = [round(x / total, 5) for x in hist]

    grey = crop.convert("L")
    thumb = list(grey.resize((_THUMB, _THUMB), Image.Resampling.BOX).getdata())

    edges = (
        grey.resize((_EDGE_GRID * 8, _EDGE_GRID * 8), Image.Resampling.BILINEAR)
        .filter(ImageFilter.FIND_EDGES)
        .resize((_EDGE_GRID, _EDGE_GRID), Image.Resampling.BOX)
    )

    return {
        "v": FEATURE_VERSION,
        "hist": hist,
        "gray": _normalise([float(x) for x in thumb]),
        "edge": _normalise([float(x) for x in edges.getdata()]),
    }


def analyse(image_bytes: bytes, roi) -> Analysis:
    """Decode, crop and describe one snapshot."""
    img = _open(image_bytes)
    crop = img.crop(_box(img, roi))
    grey = crop.convert("L")
    pixels = list(grey.resize((32, 32), Image.Resampling.BOX).getdata())
    brightness = sum(pixels) / len(pixels)

    # Enlarge small crops so they are legible in Discord.
    shown = crop
    if shown.width < 480:
        scale = 480 / shown.width
        shown = shown.resize(
            (480, max(1, int(shown.height * scale))), Image.Resampling.LANCZOS
        )
    buf = io.BytesIO()
    shown.save(buf, format="JPEG", quality=85)
    return Analysis(describe(crop), round(brightness, 1), buf.getvalue())


def roi_preview(image_bytes: bytes, roi) -> bytes:
    """The whole snapshot with the region outlined, as a JPEG."""
    img = _open(image_bytes)
    draw = ImageDraw.Draw(img)
    box = _box(img, roi)
    width = max(2, img.width // 300)
    draw.rectangle(box, outline=(255, 0, 0), width=width)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# --- comparison ---------------------------------------------------------------


def _hellinger(p: list[float], q: list[float]) -> float:
    coefficient = sum(math.sqrt(a * b) for a, b in zip(p, q))
    return math.sqrt(max(0.0, 1.0 - coefficient))


def _corr_distance(a: list[float], b: list[float]) -> float:
    """(1 - Pearson r) / 2 on already-normalised vectors: 0 same, 1 opposite."""
    count = len(a)
    if not count:
        return 1.0
    r = sum(x * y for x, y in zip(a, b)) / count
    if not any(a) or not any(b):
        # A flat crop (fog, black frame) matches nothing in shape.
        r = 0.0
    return (1.0 - max(-1.0, min(1.0, r))) / 2.0


def distance(a: dict, b: dict) -> float:
    """Combined distance between two descriptors, 0 (identical) to ~1."""
    return (
        _W_HIST * _hellinger(a["hist"], b["hist"])
        + _W_GRAY * _corr_distance(a["gray"], b["gray"])
        + _W_EDGE * _corr_distance(a["edge"], b["edge"])
    )


def usable(ref: dict, roi_key: str) -> bool:
    """Whether a stored reference can be compared under the current region."""
    features = ref.get("features") or {}
    return (
        ref.get("label") in LABELS
        and ref.get("roi") == roi_key
        and features.get("v") == FEATURE_VERSION
    )


def roi_key(roi) -> str:
    """A stable string for a region, so references remember theirs."""
    return ",".join(f"{v:.3f}" for v in clamp_roi(roi))


def classify(refs: list[dict], features: dict, k: int = 5) -> Prediction:
    """Distance-weighted k-NN over ``refs`` (already filtered by ``usable``)."""
    if not refs:
        return Prediction(None, 0.0, 1.0, 0)
    scored = sorted(
        ((distance(features, ref["features"]), ref["label"]) for ref in refs),
        key=lambda pair: pair[0],
    )
    neighbours = scored[: max(1, min(k, len(scored)))]
    votes = {label: 0.0 for label in LABELS}
    for dist, label in neighbours:
        votes[label] += 1.0 / (dist + 0.02)
    total = sum(votes.values())
    label = max(votes, key=votes.get)
    return Prediction(label, round(votes[label] / total, 3), round(scored[0][0], 4), len(refs))


def leave_one_out(refs: list[dict], k: int = 5, limit: int = 120) -> float | None:
    """Accuracy predicting each of the newest ``limit`` refs from the rest.

    None when either label has fewer than two examples (no meaningful score).
    """
    counts = {label: sum(1 for r in refs if r["label"] == label) for label in LABELS}
    if min(counts.values()) < 2:
        return None
    sample = refs[-limit:]
    correct = 0
    for index, ref in enumerate(sample):
        others = sample[:index] + sample[index + 1 :]
        if classify(others, ref["features"], k).label == ref["label"]:
            correct += 1
    return round(correct / len(sample), 3)
