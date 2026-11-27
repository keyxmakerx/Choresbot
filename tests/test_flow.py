"""End-to-end: the coordinator learning cans from Discord answers, then nagging.

Imports the integration normally; only the camera, the Store, the Discord
client and HA's timers are replaced. Needs homeassistant and discord.py
installed. Runnable with plain python3.
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


class FakeMessage:
    def __init__(self, bot, mid, kwargs) -> None:
        self.id = mid
        self.kwargs = kwargs
        self.embeds = [kwargs["embed"]] if kwargs.get("embed") else []
        self.bot = bot

    async def edit(self, embed=None, view=None):
        self.embeds = [embed]
        self.bot.edits.append((self.id, embed, view))


class FakeBot:
    is_ready = True

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.messages: dict[int, FakeMessage] = {}
        self.edits: list = []

    async def async_post(self, channel_id, **kwargs):
        mid = 1000 + len(self.posts)
        kwargs["channel_id"] = channel_id
        self.posts.append(kwargs)
        self.messages[mid] = FakeMessage(self, mid, kwargs)
        return mid

    async def async_fetch(self, channel_id, message_id):
        return self.messages[message_id]

    async def async_edit(self, channel_id, message_id, *, embed, view=None):
        await self.messages[message_id].edit(embed=embed, view=view)

    async def async_delete(self, channel_id, message_id):
        self.messages.pop(message_id, None)


class Camera:
    cans = False
    seed = 0

    async def snap(self, hass, entity_id):
        Camera.seed += 1
        return scenes.scene(Camera.cans, 5000 + Camera.seed)


def _coordinator(root: str):
    entry = types.SimpleNamespace(
        entry_id="e1",
        data={const.CONF_BOT_TOKEN: "x", const.CONF_CHANNEL_ID: "111"},
        options={
            const.CONF_CANS_ENABLED: True,
            const.CONF_CAMERA: "camera.doorbell",
            const.CONF_ROI_LEFT: scenes.ROI[0] * 100,
            const.CONF_ROI_TOP: scenes.ROI[1] * 100,
            const.CONF_ROI_RIGHT: scenes.ROI[2] * 100,
            const.CONF_ROI_BOTTOM: scenes.ROI[3] * 100,
            const.CONF_CANS_NIGHTS: list(range(7)),  # every night, so "today" counts
            const.CONF_NAG_MENTION: "<@42>",
        },
    )
    hass = FakeHass(root)
    coord = coord_mod.ChoresCoordinator(hass, entry)
    coord._store = FakeStore()
    coord._bot = FakeBot()
    coord._signal = lambda: None
    return coord


async def _label_latest(coord, label: str, who="Key"):
    sid = next(reversed(coord.state["cans"]["pending"]))
    return await coord.async_label(sid, label, who, 7, None)


def _no_timer(*_args, **_kwargs):
    return lambda: None


async def scenario() -> None:
    coord_mod._snapshot = Camera().snap
    # HA's timers need a running hass; the scenario calls the ticks directly.
    coord_mod.async_track_time_interval = _no_timer
    coord_mod.async_track_time_change = _no_timer
    coord_mod.async_call_later = _no_timer
    with tempfile.TemporaryDirectory() as root:
        coord = _coordinator(root)
        await coord.async_setup()
        for unsub in coord._unsubs:
            unsub()
        coord._unsubs.clear()
        assert coord.model.phase == trash.PHASE_EMPTY

        # Board posts and marks chores.
        await coord.async_update_board()
        assert coord.state["board"]["message_id"] == 1000
        assert await coord.async_mark_done("Trash-Ext", "Key", 7)
        assert "Trash-Ext" in coord.board_embed().description

        # Learning: every manual check posts a snapshot with label buttons.
        for i in range(6):
            for cans in (True, False):
                Camera.cans = cans
                text = await coord.async_check_cans(manual=True)
                assert "Posted the snapshot" in text, text
                embed, reply = await _label_latest(coord, "out" if cans else "in")
                assert "learned" in reply
        assert coord.model.ready, coord.model
        assert os.listdir(os.path.join(root, const.SAMPLES_DIR, "out"))

        # A label can't be given twice.
        assert await coord.async_label("deadbeef", "out", "Key", 7, None) is None

        # Cans night, cans not out: a nag that pings, with the "they're out" button.
        Camera.cans = False
        coord.state["cans"]["night"] = None
        posts_before = len(coord._bot.posts)
        await coord.async_cans_night(force=True)
        nag = coord._bot.posts[-1]
        assert len(coord._bot.posts) == posts_before + 1
        assert nag["silent"] is False and nag["content"] == "<@42>", nag
        assert "aren't out" in nag["embed"].title
        night = coord.state["cans"]["night"]
        assert night["resolved"] is None

        # Someone taps "They're out": resolved, Trash ticked on the board.
        embed = await coord.async_nag_action(night["key"], "out", "Sam", 9, nag["embed"])
        assert "Sam" in embed.description
        assert coord.state["chores"]["trash"]["last_by"] == "Sam"
        # The follow-up stays quiet once resolved.
        await coord._renag_later(night["key"])(None)
        assert len(coord._bot.posts) == posts_before + 1

        # Next cans night, cans already out: the camera resolves it, no post.
        coord.state["cans"]["night"] = None
        Camera.cans = True
        posts_before = len(coord._bot.posts)
        await coord.async_cans_night(force=True)
        new_posts = coord._bot.posts[posts_before:]
        assert all(p.get("content") != "<@42>" for p in new_posts), new_posts
        assert coord.state["cans"]["night"]["resolved"] == "out"

        # After dark: no judgement, and the reminder asks instead of nagging.
        coord.state["cans"]["night"] = None
        coord.hass.states.elevation = -5
        coord._last_crop = None
        coord.state["cans"]["last"] = None  # nothing seen in daylight today
        Camera.cans = False
        assert "Sun's down" in await coord.async_check_cans(manual=False)
        await coord.async_cans_night(force=True)
        ask = coord._bot.posts[-1]
        assert "are the cans out?" in ask["embed"].title
        assert not ask.get("files")  # no usable picture at night

        # Changing the region sets learning aside (references remember theirs).
        coord.roi_key = "0.000,0.000,0.500,0.500"
        await coord._async_refresh_model()
        assert coord.model.phase == trash.PHASE_EMPTY

        await coord.async_reset_cans()
        assert coord.state["cans"]["refs"] == []
        print("ok  scenario")


if __name__ == "__main__":
    asyncio.run(scenario())
    print("1 passed")
