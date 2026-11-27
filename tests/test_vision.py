"""Tests for the local can detector, on synthetic street scenes.

Runnable with plain ``python3 tests/test_vision.py`` (needs only Pillow).
"""

from __future__ import annotations

import importlib.util
import io
import os
import random
import sys
import time

from PIL import Image, ImageDraw, ImageFilter

_PATH = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "chores_discord", "vision.py"
)
_spec = importlib.util.spec_from_file_location("cd_vision", _PATH)
vision = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vision
_spec.loader.exec_module(vision)

# Cans sit in the lower-right quarter of a doorbell's view, far away.
ROI = (0.70, 0.55, 0.90, 0.80)
KEY = vision.roi_key(ROI)


def scene(cans: bool, seed: int, light: float = 1.0, size=(1920, 1080)) -> bytes:
    """A crude street: sky, lawn, curb, road, maybe two cans; random clutter."""
    rng = random.Random(seed)
    width, height = size
    img = Image.new("RGB", size, (120, 160, 210))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, int(height * 0.45), width, height), fill=(70, 120, 60))  # lawn
    draw.rectangle((0, int(height * 0.72), width, height), fill=(95, 95, 100))  # road
    draw.rectangle((0, int(height * 0.70), width, int(height * 0.72)), fill=(170, 170, 170))
    # A shadow or parked car that moves around between shots.
    x = rng.randint(0, width - 400)
    draw.rectangle((x, int(height * 0.74), x + 380, int(height * 0.86)), fill=(
        rng.randint(30, 200), rng.randint(30, 200), rng.randint(30, 200)))
    if cans:
        base_x = int(width * 0.75) + rng.randint(-25, 25)
        top = int(height * 0.60) + rng.randint(-10, 10)
        for offset, colour in ((0, (25, 80, 35)), (95, (30, 60, 140))):
            draw.rectangle(
                (base_x + offset, top, base_x + offset + 80, int(height * 0.71)),
                fill=colour,
                outline=(10, 10, 10),
                width=3,
            )
    # Lighting change and sensor noise.
    img = img.point(lambda v: max(0, min(255, int(v * light))))
    noise = Image.effect_noise(size, 18).convert("RGB")
    img = Image.blend(img, noise, 0.08).filter(ImageFilter.GaussianBlur(1))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def ref(cans: bool, seed: int, light: float = 1.0) -> dict:
    analysis = vision.analyse(scene(cans, seed, light), ROI)
    return {"label": "out" if cans else "in", "roi": KEY, "features": analysis.features}


def test_clamp_roi() -> None:
    assert vision.clamp_roi((0.9, 0.8, 0.1, 0.2)) == (0.1, 0.2, 0.9, 0.8)
    left, top, right, bottom = vision.clamp_roi((0.5, 0.5, 0.5, 0.5))
    assert right - left >= 0.0199 and bottom - top >= 0.0199
    assert vision.clamp_roi((-1, -1, 2, 2)) == (0.0, 0.0, 1.0, 1.0)


def test_analyse_outputs() -> None:
    result = vision.analyse(scene(True, 1), ROI)
    f = result.features
    assert f["v"] == vision.FEATURE_VERSION
    assert abs(sum(f["hist"]) - 1.0) < 0.01
    assert len(f["gray"]) == 256 and len(f["edge"]) == 64
    assert 0 < result.brightness < 255
    assert result.crop_jpeg[:2] == b"\xff\xd8"
    assert Image.open(io.BytesIO(result.crop_jpeg)).width >= 480


def test_dark_crop_is_dark() -> None:
    lit = vision.analyse(scene(False, 2), ROI).brightness
    dim = vision.analyse(scene(False, 2, light=0.12), ROI).brightness
    assert dim < 30 < lit, (dim, lit)


def test_distance_properties() -> None:
    a = vision.analyse(scene(True, 3), ROI).features
    b = vision.analyse(scene(True, 4), ROI).features
    c = vision.analyse(scene(False, 5), ROI).features
    assert vision.distance(a, a) < 0.02  # rounding in stored vectors
    assert vision.distance(a, b) < vision.distance(a, c)


def test_classify_empty() -> None:
    p = vision.classify([], {"hist": [], "gray": [], "edge": []})
    assert p.label is None and p.compared == 0


def test_learns_from_few_labels() -> None:
    refs = [ref(True, s) for s in range(10, 15)] + [ref(False, s) for s in range(20, 25)]
    correct = 0
    trials = 0
    for seed in range(100, 120):
        for cans in (True, False):
            light = 0.7 + (seed % 5) * 0.12  # overcast to bright
            p = vision.classify(refs, vision.analyse(scene(cans, seed, light), ROI).features)
            trials += 1
            correct += p.label == ("out" if cans else "in")
    assert correct / trials >= 0.9, (correct, trials)


def test_leave_one_out() -> None:
    assert vision.leave_one_out([ref(True, 1), ref(False, 2)]) is None
    refs = [ref(True, s) for s in range(30, 36)] + [ref(False, s) for s in range(40, 46)]
    acc = vision.leave_one_out(refs)
    assert acc is not None and acc >= 0.9, acc


def test_usable_filters_region_and_version() -> None:
    good = ref(True, 7)
    assert vision.usable(good, KEY)
    assert not vision.usable(good, vision.roi_key((0, 0, 0.5, 0.5)))
    stale = {**good, "features": {**good["features"], "v": 0}}
    assert not vision.usable(stale, KEY)


def test_preview_is_jpeg() -> None:
    out = vision.roi_preview(scene(True, 8), ROI)
    assert Image.open(io.BytesIO(out)).size[0] > 0


def test_cheap_enough() -> None:
    image = scene(True, 9)
    refs = [ref(True, s) for s in range(50, 60)] + [ref(False, s) for s in range(60, 70)]
    start = time.perf_counter()
    for _ in range(5):
        vision.classify(refs, vision.analyse(image, ROI).features)
    per = (time.perf_counter() - start) / 5
    # Generous: a dev box does this in ~20 ms; the Green is a few times slower.
    assert per < 0.5, per


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
