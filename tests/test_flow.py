"""End-to-end: the coordinator learning both bins from answers, then nagging.

Imports the integration normally; only the camera, the Store, the Discord
client and HA's timers are replaced. The cards are real discord.py
Components V2 views, checked against Discord's component limits. Needs
homeassistant and discord.py installed. Runnable with plain python3.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, HERE)

from custom_components.chores_discord import coordinator as coord_mod  # noqa: E402
from custom_components.chores_discord import const, trash  # noqa: E402
import test_vision as scenes  # noqa: E402

# Discord's limit for a Components V2 message, nested components included.
MAX_COMPONENTS = 40


def _count(components) -> int:
    total = 0
    for comp in components:
        total += 1
        for key in ("components",):
            if key in comp:
                total += _count(comp[key])
        if "accessory" in comp:
            total += 1
    return total


def _text(view) -> str:
    """Every text display and button label in a card, for assertions."""
    out = []

    def walk(items):
        for comp in items:
            if comp.get("content"):
                out.append(comp["content"])
            if comp.get("label"):
                out.append(comp["label"])
            walk(comp.get("components", []))

    walk(view.to_components())
    return "\n".join(out)


def _custom_ids(view) -> list[str]:
    ids = []

    def walk(items):
        for comp in items:
            if comp.get("custom_id"):
                ids.append(comp["custom_id"])
            walk(comp.get("components", []))

    walk(view.to_components())
    return ids


class FakeState:
    def __init__(self, elevation: float) -> None:
        self.attributes = {"elevation": elevation}


class FakeStates:
    def __init__(self) -> None:
        self.elevation = 30.0

    def get(self, entity_id):
        return FakeState(self.elevation) if entity_id == "sun.sun" else None


class FakeHass:
    def __init__(self, root: str) -> None:
        self.states = FakeStates()
        self.config = types.SimpleNamespace(
            path=lambda *p: os.path.join(root, *p), config_dir=root
        )
        self.data = {}

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class FakeStore:
    def __init__(self) -> None:
        self.saved = None

    async def async_load(self):
        return self.saved

    def async_delay_save(self, fn, _delay):
        self.saved = fn()

    async def async_save(self, data):
        self.saved = data


class FakeBot:
    is_ready = True

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.views: dict[int, object] = {}
        self.edits: list = []

    async def async_post(self, channel_id, view, *, files=None, silent=True, mention=False):
        mid = 1000 + len(self.posts)
        count = _count(view.to_components())
        assert count <= MAX_COMPONENTS, count
        self.posts.append({"id": mid, "view": view, "files": files, "silent": silent,
                           "mention": mention, "text": _text(view)})
        self.views[mid] = view
        return mid

    async def async_edit(self, channel_id, message_id, view):
        assert _count(view.to_components()) <= MAX_COMPONENTS
        self.views[message_id] = view
        self.edits.append(message_id)

    async def async_edit_keeping(self, channel_id, message_id, build, filename="cans.jpg"):
        view = build("https://cdn.example/cans.jpg")
        if view is not None:
            await self.async_edit(channel_id, message_id, view)

    async def async_delete(self, channel_id, message_id):
        self.views.pop(message_id, None)


class Camera:
    trash = False
    recycling = False
    seed = 0

    async def snap(self, hass, entity_id):
        Camera.seed += 1
        return scenes.scene(Camera.trash, 5000 + Camera.seed, recycling=Camera.recycling)


def _no_timer(*_args, **_kwargs):
    return lambda: None


def _coordinator(root: str):
    entry = types.SimpleNamespace(
        entry_id="e1",
        data={const.CONF_BOT_TOKEN: "x", const.CONF_CHANNEL_ID: "111"},
        options={
            const.CONF_CAMERA: "camera.doorbell",
            const.CONF_CANS_ENABLED: True,
            const.CONF_ROI_LEFT: scenes.ROI[0] * 100,
            const.CONF_ROI_TOP: scenes.ROI[1] * 100,
            const.CONF_ROI_RIGHT: scenes.ROI[2] * 100,
            const.CONF_ROI_BOTTOM: scenes.ROI[3] * 100,
            const.CONF_CANS_NIGHTS: list(range(7)),
            const.CONF_RECYCLING_NIGHTS: list(range(7)),
            const.CONF_NAG_MENTION: "<@42>",
        },
    )
    coord = coord_mod.ChoresCoordinator(FakeHass(root), entry)
    coord._store = FakeStore()
    coord._bot = FakeBot()
    coord._signal = lambda: None
    return coord


async def scenario() -> None:
    coord_mod._snapshot = Camera().snap
    coord_mod.async_track_time_interval = _no_timer
    coord_mod.async_track_time_change = _no_timer
    coord_mod.async_call_later = _no_timer
    with tempfile.TemporaryDirectory() as root:
        coord = _coordinator(root)
        await coord.async_setup()
        bot = coord._bot

        # The board: one card, quick reports, chore menus.
        await coord.async_update_board()
        board = bot.posts[0]
        ids = _custom_ids(board["view"])
        assert "chores:quick:trash" in ids and "chores:quick:recycling" in ids, ids
        assert "chores:board:done" in ids and "chores:board:check" in ids
        assert "Recycling" in board["text"] and "Trash-Ext" in board["text"]

        # A quick report puts the chore at the top, flagged with who said so.
        await coord.async_report("recycling", "Sam")
        text = _text(coord.board_layout())
        assert "Needs doing" in text and "Sam says it needs doing" in text, text
        assert await coord.async_mark_done("recycling", "Alex", 3)
        assert "Sam says" not in _text(coord.board_layout())

        # Learning: each manual look posts one card asking about both bins.
        combos = [(True, True), (True, False), (False, True), (False, False)]
        for round_ in range(4):
            for trash_out, recycling_out in combos:
                Camera.trash, Camera.recycling = trash_out, recycling_out
                text = await coord.async_check_cans(manual=True)
                assert "Posted the snapshot" in text, text
                sid = next(reversed(coord.state["cans"]["pending"]))
                card = bot.posts[-1]
                assert card["files"][0][0] == "cans.jpg"
                assert f"chores:cans:trash:out:{sid}" in _custom_ids(card["view"])
                reply = await coord.async_label(sid, "trash", "out" if trash_out else "in", "Key", 7)
                assert "learned" in reply, reply
                # The card re-renders with the answer and only recycling left to ask.
                left = _custom_ids(bot.views[card["id"]])
                assert f"chores:cans:recycling:out:{sid}" in left and f"chores:cans:trash:out:{sid}" not in left
                await coord.async_label(sid, "recycling", "out" if recycling_out else "in", "Key", 7)
                assert coord.state["cans"]["pending"][sid]["closed"]
                assert not any(i.startswith("chores:cans:") for i in _custom_ids(bot.views[card["id"]]))
        assert coord.models["trash"].ready, coord.models
        assert coord.models["recycling"].ready, coord.models
        assert len(os.listdir(os.path.join(root, const.SAMPLES_DIR, "samples"))) == 16

        # An answer can't be given twice, and unknown samples are refused.
        assert await coord.async_label(sid, "trash", "out", "Key", 7) is None
        assert await coord.async_label("deadbeef", "trash", "out", "Key", 7) is None

        # "Check the bins" is private and per person.
        posts = len(bot.posts)
        Camera.trash, Camera.recycling = True, False
        view, files = await coord.async_inspect(admin=False)
        text = _text(view)
        assert not files and not _custom_ids(view), text
        assert "Trash" in text and "out" in text and "% sure" in text, text
        view, files = await coord.async_inspect(admin=True)
        assert [f[0] for f in files] == ["region.jpg", "crop.jpg"]
        assert _count(view.to_components()) <= MAX_COMPONENTS
        text = _text(view)
        assert "closest" in text and "self-test" in text and "brightness" in text, text
        sid = next(reversed(coord.state["cans"]["pending"]))
        assert f"chores:cans:trash:out:{sid}" in _custom_ids(view)
        assert await coord.async_label(sid, "trash", "out", "Key", 7, edit_message=False)
        again = coord.inspect_layout(sid, "https://cdn/r.jpg", "https://cdn/c.jpg")
        assert "says Key" in _text(again) and f"chores:cans:trash:out:{sid}" not in _custom_ids(again)
        assert len(bot.posts) == posts  # nothing went to the channel
        assert coord.is_bin_admin(1, [], True) and not coord.is_bin_admin(1, [], False)
        coord.bin_admins = coord_mod.parse_ids("<@123456789012345678> <@&223456789012345678>")
        assert coord.is_bin_admin(123456789012345678, [], False)
        assert coord.is_bin_admin(5, [223456789012345678], False)
        assert not coord.is_bin_admin(5, [], True)
        coord.bin_admins = set()

        # Bin night: trash out, recycling not. Only recycling is nagged.
        Camera.trash, Camera.recycling = True, False
        coord.state["cans"]["nights"] = {}
        before = len(bot.posts)
        await coord.async_cans_night(bins=["trash", "recycling"])
        assert coord.state["cans"]["nights"]["trash"]["resolved"] == "out"
        assert coord.state["chores"]["trash"]["last_by"] == "📷 the camera"
        nag = bot.posts[-1]
        assert len(bot.posts) == before + 1
        assert nag["silent"] is False and nag["mention"] and "<@42>" in nag["text"], nag
        assert "not out yet" in nag["text"] and "✅ out" in nag["text"], nag["text"]
        key = coord.state["cans"]["nag"]["key"]
        assert f"chores:nag:recycling:out:{key}" in _custom_ids(nag["view"])
        assert f"chores:nag:trash:out:{key}" not in _custom_ids(nag["view"])

        # Someone taps "Recycling is out": resolved and the chore ticked.
        assert await coord.async_nag_action(key, "recycling", "out", "Sam", 9)
        assert coord.state["chores"]["recycling"]["last_by"] == "Sam"
        assert not _custom_ids(coord.nag_layout(key, None))[:1] or all(
            not i.startswith("chores:nag:") for i in _custom_ids(coord.nag_layout(key, None))
        )
        # The follow-up stays quiet once everything is resolved.
        await coord._renag_later(key)(None)
        assert len(bot.posts) == before + 1

        # After dark: nothing is judged, and the reminder asks instead of nagging.
        coord.state["cans"]["nights"] = {}
        coord.state["cans"]["last"] = None
        coord._last_crop = None
        coord.hass.states.elevation = -5
        assert "Sun's down" in await coord.async_check_cans(manual=False)
        await coord.async_cans_night(bins=["trash"])
        ask = bot.posts[-1]
        assert "is it out?" in ask["text"], ask["text"]
        assert not ask["files"]

        # The training page: samples with model opinions, relabel and delete.
        coord.hass.states.elevation = 30
        samples = await coord.async_panel_samples()
        assert len(samples) == 33 and all(s["thumb"] for s in samples)
        target = samples[0]
        flipped = "in" if target["label"] == "out" else "out"
        assert await coord.async_panel_relabel(target["id"], flipped)
        again = {s["id"]: s for s in await coord.async_panel_samples()}
        assert again[target["id"]]["label"] == flipped
        assert again[target["id"]]["model_says"] == target["label"]  # it now disagrees
        assert await coord.async_panel_delete(target["id"])
        assert len(await coord.async_panel_samples()) == 32
        snap = await coord.async_panel_snapshot()
        assert snap["sid"] and snap["frame"] and set(snap["verdicts"]) == {"trash", "recycling"}
        overview = await coord.async_panel_overview()
        assert overview["bins"]["recycling"]["ready"] and overview["pending"][0]["sid"] == snap["sid"]
        assert await coord.async_label(snap["sid"], "trash", "out", "Key", None)

        # Changing the region sets learning aside (references remember theirs).
        coord.roi_key = "0.000,0.000,0.500,0.500"
        await coord._async_refresh_models()
        assert coord.models["trash"].phase == trash.PHASE_EMPTY

        await coord.async_reset_cans()
        assert coord.state["cans"]["refs"] == []
        assert os.listdir(os.path.join(root, const.SAMPLES_DIR, "samples")) == []
        print("ok  scenario")


if __name__ == "__main__":
    asyncio.run(scenario())
    print("1 passed")
