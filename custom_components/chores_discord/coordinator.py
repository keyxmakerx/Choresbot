"""State, timers and the glue between Home Assistant, the rules and Discord."""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
import logging
import os
import re
import time as time_mod
from typing import Any

import discord

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import chores as chores_mod, const as C, trash, vision
from .discord_bot import (
    AskCard,
    BinRow,
    BoardCard,
    DiscordBot,
    InspectCard,
    NagCard,
    ask_view,
    board_view,
    inspect_view,
    nag_view,
    preview_view,
    status_view,
)

_LOGGER = logging.getLogger(__name__)

type ChoresConfigEntry = ConfigEntry["ChoresCoordinator"]

# Card accent colours.
RED = 0xE5484D
ORANGE = 0xF07F2E
YELLOW = 0xF2C230
GREEN = 0x30A46C
BLUE = 0x3E8ED0
GREY = 0x8B939C
_STATUS_COLOUR = {
    chores_mod.REPORTED: RED,
    chores_mod.OVERDUE: RED,
    chores_mod.DUE: ORANGE,
    chores_mod.SOON: YELLOW,
}

# A snapshot this recent can be reused for the night reminder.
_CROP_REUSE_S = 15 * 60
_CAMERA_TIMEOUT = 15

RESULT_TEXT = {
    trash.OUT: "out",
    trash.NOT_OUT: "not out",
    trash.UNSURE: "not sure",
    trash.DARK: "too dark to see",
    trash.UNKNOWN: "not checked yet",
}


def parse_ids(text: str) -> set[int]:
    """Discord ids from free text: plain ids, <@user> or <@&role> mentions."""
    return {int(m) for m in re.findall(r"\d{15,22}", str(text))}


def _blank_state() -> dict:
    return {
        "chores": {},
        "board": {"message_id": None},
        "cans": {"refs": [], "pending": {}, "last": None, "asks": [], "nights": {}, "nag": None},
    }


def _b64(data: bytes | None) -> str | None:
    return base64.b64encode(data).decode() if data else None


async def _snapshot(hass: HomeAssistant, entity_id: str) -> bytes:
    """One still from the camera. Imported here: the camera component pulls in
    the stream stack, which isn't needed until the watcher is actually used."""
    from homeassistant.components.camera import async_get_image  # noqa: PLC0415

    image = await async_get_image(hass, entity_id, timeout=_CAMERA_TIMEOUT)
    return image.content


class ChoresCoordinator:
    """Owns the stored state and every timer; the Discord layer calls in."""

    def __init__(self, hass: HomeAssistant, entry: ChoresConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        opts = {**entry.data, **entry.options}
        self._opts = opts
        self.channel_id = int(opts[C.CONF_CHANNEL_ID])
        cans_channel = str(opts.get(C.CONF_CANS_CHANNEL_ID) or "").strip()
        self.cans_channel_id = int(cans_channel) if cans_channel.isdigit() else self.channel_id

        self.chores = chores_mod.catalogue(
            opts.get(C.CONF_CUSTOM_CHORES, C.DEFAULT_CUSTOM_CHORES),
            list(opts.get(C.CONF_DISABLED_CHORES) or []),
        )
        self.quick = chores_mod.quick_chores(
            self.chores, list(opts.get(C.CONF_QUICK) or chores_mod.DEFAULT_QUICK)
        )
        self.camera = opts.get(C.CONF_CAMERA) or ""
        self.cans_enabled = bool(opts.get(C.CONF_CANS_ENABLED, bool(self.camera))) and bool(
            self.camera
        )
        self.roi_percent = tuple(
            float(opts.get(key, default))
            for key, default in zip(
                (C.CONF_ROI_LEFT, C.CONF_ROI_TOP, C.CONF_ROI_RIGHT, C.CONF_ROI_BOTTOM),
                C.DEFAULT_ROI,
            )
        )
        self.roi = vision.clamp_roi(tuple(v / 100.0 for v in self.roi_percent))
        self.roi_key = vision.roi_key(self.roi)
        self.nights = {
            trash.BIN_TRASH: [int(d) for d in opts.get(C.CONF_CANS_NIGHTS) or []],
            trash.BIN_RECYCLING: [int(d) for d in opts.get(C.CONF_RECYCLING_NIGHTS) or []],
        }
        self.weeks = {
            trash.BIN_TRASH: trash.WEEKS_EVERY,
            trash.BIN_RECYCLING: opts.get(C.CONF_RECYCLING_WEEKS, C.DEFAULT_RECYCLING_WEEKS),
        }
        self.bin_chore = {
            trash.BIN_TRASH: opts.get(C.CONF_CANS_CHORE, C.DEFAULT_CANS_CHORE) or C.NO_CHORE,
            trash.BIN_RECYCLING: opts.get(C.CONF_RECYCLING_CHORE, C.DEFAULT_RECYCLING_CHORE)
            or C.NO_CHORE,
        }
        self.nag_mention = str(opts.get(C.CONF_NAG_MENTION) or "").strip()
        self.bin_admins = parse_ids(opts.get(C.CONF_BIN_ADMINS) or "")
        self.confidence = float(opts.get(C.CONF_CONFIDENCE, C.DEFAULT_CONFIDENCE)) / 100.0
        self.min_labels = int(opts.get(C.CONF_MIN_LABELS, C.DEFAULT_MIN_LABELS))
        self.min_accuracy = float(opts.get(C.CONF_MIN_ACCURACY, C.DEFAULT_MIN_ACCURACY)) / 100.0
        self.min_elevation = float(opts.get(C.CONF_MIN_ELEVATION, C.DEFAULT_MIN_ELEVATION))
        self.min_brightness = float(opts.get(C.CONF_MIN_BRIGHTNESS, C.DEFAULT_MIN_BRIGHTNESS))

        self.state: dict[str, Any] = _blank_state()
        self.models = {
            b: trash.model_status({}, None, self.min_labels, self.min_accuracy) for b in trash.BINS
        }
        self._store: Store = Store(hass, C.STORAGE_VERSION, f"{C.STORAGE_KEY}.{entry.entry_id}")
        self._bot = DiscordBot(self, opts[C.CONF_BOT_TOKEN])
        self._unsubs: list = []
        self._board_lock = asyncio.Lock()
        self._cans_lock = asyncio.Lock()
        self._last_crop: tuple[float, bytes] | None = None

    # --- lifecycle -------------------------------------------------------------

    async def async_setup(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            base = _blank_state()
            for key, value in stored.items():
                if isinstance(base.get(key), dict) and isinstance(value, dict):
                    base[key].update(value)
            self.state = base
        self._migrate()
        await self._async_refresh_models()

        self._unsubs.append(
            async_track_time_interval(self.hass, self._tick_board, timedelta(hours=1))
        )
        digest = trash.parse_time(self._opts.get(C.CONF_DIGEST_TIME, C.DEFAULT_DIGEST_TIME), None)
        if digest and self._opts.get(C.CONF_DIGEST, C.DEFAULT_DIGEST):
            self._unsubs.append(
                async_track_time_change(
                    self.hass, self._tick_digest, hour=digest.hour, minute=digest.minute, second=0
                )
            )
        if self.cans_enabled:
            minutes = int(self._opts.get(C.CONF_CHECK_INTERVAL, C.DEFAULT_CHECK_INTERVAL))
            self._unsubs.append(
                async_track_time_interval(
                    self.hass, self._tick_cans, timedelta(minutes=max(5, minutes))
                )
            )
            if any(self.nights.values()):
                nag = trash.parse_time(self._opts.get(C.CONF_NAG_TIME, C.DEFAULT_NAG_TIME), None)
                if nag:
                    self._unsubs.append(
                        async_track_time_change(
                            self.hass, self._tick_night, hour=nag.hour, minute=nag.minute, second=0
                        )
                    )

    def _migrate(self) -> None:
        """Bring older stored state up to date (all changes are additive)."""
        cans = self.state["cans"]
        for ref in cans["refs"]:
            ref.setdefault("bin", trash.BIN_TRASH)
            ref.setdefault("sid", trash.new_sample_id())
        if not isinstance(cans.get("nights"), dict):
            cans["nights"] = {}
        cans.pop("night", None)
        last = cans.get("last")
        if isinstance(last, dict) and not isinstance(last.get("result"), dict):
            cans["last"] = None
        for sid, sample in list(cans["pending"].items()):
            if "answers" not in sample:
                cans["pending"].pop(sid)

    async def async_run_bot(self) -> None:
        """Run the gateway; errors are logged, never raised into HA."""
        try:
            await self._bot.async_start()
        except asyncio.CancelledError:
            raise
        except discord.LoginFailure:
            _LOGGER.error("Discord rejected the bot token; reconfigure the integration")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Discord bot stopped")

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self.state)
        await self._bot.async_close()

    async def async_on_bot_ready(self) -> None:
        """Make sure the board exists and is current after (re)connecting."""
        await self.async_update_board()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self.state, 5)

    @callback
    def _signal(self) -> None:
        async_dispatcher_send(self.hass, f"{C.SIGNAL_UPDATE}_{self.entry.entry_id}")

    def _changed(self) -> None:
        self._save()
        self._signal()

    # --- chores ----------------------------------------------------------------

    def find_chore(self, key_or_name: str) -> chores_mod.Chore | None:
        wanted = chores_mod.slugify(key_or_name)
        for chore in self.chores:
            if chore.key in (key_or_name, wanted):
                return chore
        return None

    async def async_mark_done(
        self, key: str, who: str, uid: int | None, *, refresh: bool = True
    ) -> chores_mod.Chore | None:
        chore = self.find_chore(key)
        if chore is None:
            return None
        chores_mod.mark_done(self.state["chores"], chore.key, time_mod.time(), who, uid)
        self._changed()
        if refresh:
            await self.async_update_board()
        return chore

    async def async_toggle_claim(
        self, key: str, who: str, uid: int | None
    ) -> tuple[chores_mod.Chore, bool] | None:
        chore = self.find_chore(key)
        if chore is None:
            return None
        claimed = chores_mod.toggle_claim(self.state["chores"], chore.key, time_mod.time(), who, uid)
        self._changed()
        return chore, claimed

    async def async_report(self, key: str, who: str) -> tuple[chores_mod.Chore, bool] | None:
        chore = self.find_chore(key)
        if chore is None:
            return None
        fresh = chores_mod.report(self.state["chores"], chore.key, time_mod.time(), who)
        self._changed()
        return chore, fresh

    async def async_undo(self, key: str, uid: int | None) -> bool:
        ok = chores_mod.undo(self.state["chores"], key, uid)
        if ok:
            self._changed()
            await self.async_update_board()
        return ok

    def overdue(self) -> list[chores_mod.Chore]:
        return chores_mod.overdue(self.chores, self.state["chores"], time_mod.time())

    def _bins_text(self, now: float) -> str:
        last = self.state["cans"].get("last") or {}
        when = ""
        if last.get("at"):
            span = chores_mod.ago(now - last["at"])
            when = " (just now)" if span == "just now" else f" ({span} ago)"
        lines = []
        for b in trash.BINS:
            result = (last.get("result") or {}).get(b, trash.UNKNOWN)
            nights = ", ".join(C.WEEKDAYS[d][:3] for d in sorted(self.nights[b])) or "no night set"
            if b == trash.BIN_RECYCLING and self.weeks[b] != trash.WEEKS_EVERY:
                nights += f" ({self.weeks[b]} weeks)"
            lines.append(
                f"{trash.BIN_EMOJI[b]} **{trash.BIN_NAME[b]}** · {RESULT_TEXT.get(result, result)}"
                f"{when} · {nights}"
            )
        learning = " · ".join(
            f"{trash.BIN_NAME[b].lower()} {self.models[b].out}/{self.models[b].not_out}"
            f"{' ✓' if self.models[b].ready else ''}"
            for b in trash.BINS
        )
        return "\n".join(lines) + f"\n-# Learning (out/not out): {learning}"

    def board_layout(self):
        now = time_mod.time()
        state = self.state["chores"]
        worst = chores_mod.worst(self.chores, state, now)
        local = dt_util.now()
        stamp = f"{local:%a} {local.hour % 12 or 12}:{local:%M %p}"
        card = BoardCard(
            colour=_STATUS_COLOUR.get(worst, GREEN),
            title="🧹 Chores",
            sections=chores_mod.board_sections(self.chores, state, now),
            quick=[(c.key, c.emoji, chores_mod.short_name(c.name)) for c in self.quick],
            chores=[(c.key, c.emoji, c.name, c.note) for c in self.chores],
            bins_text=self._bins_text(now) if self.cans_enabled else None,
            cans_enabled=self.cans_enabled,
            footer=f"Updated {stamp} · menus mark a chore done or claim it",
        )
        return board_view(card)

    async def async_update_board(self, *, repost: bool = False) -> None:
        """Edit the board in place, or post it if it's missing (or ``repost``)."""
        if not self._bot.is_ready:
            return
        async with self._board_lock:
            board = self.state["board"]
            message_id = board.get("message_id")
            if message_id and not repost:
                try:
                    await self._bot.async_edit(self.channel_id, message_id, self.board_layout())
                    return
                except discord.NotFound:
                    message_id = None
                except Exception:  # noqa: BLE001
                    _LOGGER.warning("Could not edit the chore board", exc_info=True)
                    return
            if message_id and repost:
                try:
                    await self._bot.async_delete(self.channel_id, message_id)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Could not delete the old board", exc_info=True)
            try:
                board["message_id"] = await self._bot.async_post(
                    self.channel_id, self.board_layout()
                )
                self._save()
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Could not post the chore board", exc_info=True)

    async def _tick_board(self, _now) -> None:
        await self.async_update_board()

    async def _tick_digest(self, _now) -> None:
        """Daily: move the board to the bottom of the channel (silently)."""
        await self.async_update_board(repost=True)

    # --- bins: model -------------------------------------------------------------

    def _refs(self, bin_name: str) -> list[dict]:
        return [
            r for r in trash.for_bin(self.state["cans"]["refs"], bin_name)
            if vision.usable(r, self.roi_key)
        ]

    async def _async_refresh_models(self, bins=trash.BINS) -> None:
        for b in bins:
            refs = self._refs(b)
            accuracy = await self.hass.async_add_executor_job(vision.leave_one_out, refs)
            self.models[b] = trash.model_status(
                trash.label_counts(refs), accuracy, self.min_labels, self.min_accuracy
            )

    def _summary(self) -> str:
        return " · ".join(f"{trash.BIN_NAME[b]}: {self.models[b].summary()}" for b in trash.BINS)

    def _sun_elevation(self) -> float | None:
        sun = self.hass.states.get("sun.sun")
        if sun is None:
            return None
        try:
            return float(sun.attributes.get("elevation"))
        except (TypeError, ValueError):
            return None

    def bins_tonight(self, local=None) -> list[str]:
        local = local or dt_util.now()
        return [
            b for b in trash.BINS
            if trash.is_cans_night(local, self.nights[b], self.weeks[b])
        ]

    # --- bins: looking -------------------------------------------------------------

    async def _tick_cans(self, _now) -> None:
        try:
            await self.async_check_cans(manual=False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Scheduled bin check failed")

    async def _async_look(self) -> tuple[vision.Analysis, bytes, dict, bool]:
        """Snapshot, analyse, classify every bin. Returns (analysis, image,
        per-bin {label, confidence, nearest, result}, daylight)."""
        content = await _snapshot(self.hass, self.camera)
        analysis = await self.hass.async_add_executor_job(vision.analyse, content, self.roi)
        daylight = trash.is_daylight(
            self._sun_elevation(), analysis.brightness, self.min_elevation, self.min_brightness
        )
        verdicts: dict[str, dict] = {}
        for b in trash.BINS:
            prediction = await self.hass.async_add_executor_job(
                vision.classify, self._refs(b), analysis.features
            )
            if not daylight:
                result = trash.DARK
            elif self.models[b].ready:
                result = trash.verdict(prediction.label, prediction.confidence, self.confidence)
            else:
                result = trash.UNSURE
            verdicts[b] = {
                "label": prediction.label,
                "confidence": prediction.confidence,
                "nearest": prediction.nearest,
                "compared": prediction.compared,
                "result": result,
            }
        return analysis, content, verdicts, daylight

    def _record(self, analysis: vision.Analysis, verdicts: dict, daylight: bool) -> None:
        now = time_mod.time()
        self.state["cans"]["last"] = {
            "at": now,
            "result": {b: v["result"] for b, v in verdicts.items()},
            "guess": {b: {"label": v["label"], "confidence": v["confidence"]} for b, v in verdicts.items()},
            "brightness": analysis.brightness,
            "daylight": daylight,
            # Kept so the night reminder can turn this look into a label.
            "features": analysis.features if daylight else None,
        }
        if daylight:
            self._last_crop = (now, analysis.crop_jpeg)

    async def async_check_cans(self, *, manual: bool, allow_ask: bool = True) -> str:
        """Take a snapshot, classify it, maybe ask for labels. Returns a summary."""
        if not self.cans_enabled:
            return "Bin watching is off. Pick a camera under Configure → Bin watcher."
        elevation = self._sun_elevation()
        if not manual and elevation is not None and elevation < self.min_elevation:
            return "Sun's down; not looking."
        if self._cans_lock.locked():
            return "Already checking — one moment."
        async with self._cans_lock:
            try:
                analysis, _content, verdicts, daylight = await self._async_look()
            except HomeAssistantError as err:
                _LOGGER.warning("No snapshot from %s: %s", self.camera, err)
                return f"Couldn't get a snapshot from {self.camera}."
            self._record(analysis, verdicts, daylight)
            if not daylight:
                self._changed()
                return "Too dark to see the bins right now. I only judge them in daylight."

            now = time_mod.time()
            local = dt_util.now()
            tonight = self.bins_tonight(local)
            cans = self.state["cans"]
            any_unsure = any(v["result"] == trash.UNSURE for v in verdicts.values())
            asked = False
            if allow_ask and (
                manual
                or trash.should_ask(
                    now=now,
                    result=trash.UNSURE if any_unsure else trash.OUT,
                    ready=all(m.ready for m in self.models.values()),
                    asks=cans["asks"],
                    max_per_day=int(self._opts.get(C.CONF_ASKS_PER_DAY, C.DEFAULT_ASKS_PER_DAY)),
                    min_gap_hours=float(self._opts.get(C.CONF_ASK_GAP, C.DEFAULT_ASK_GAP)),
                    cans_night=bool(tonight),
                )
            ):
                asked = await self._async_ask(analysis, verdicts, tonight, trash.night_key(local))
                if asked and not manual:
                    cans["asks"] = [t for t in cans["asks"] if now - t < trash.DAY] + [now]

            await self._async_resolve_seen(verdicts, local)
            self._changed()

        text = " ".join(
            f"{trash.BIN_EMOJI[b]} {self._guess_text(b, v)}" for b, v in verdicts.items()
        )
        if asked:
            text += " Posted the snapshot — tap what you see."
        return text

    async def _async_resolve_seen(self, verdicts: dict, local) -> None:
        """A bin seen out on its night closes that night (and ticks its chore)."""
        for b in self.bins_tonight(local):
            if verdicts[b]["result"] == trash.OUT:
                await self._async_resolve(b, trash.night_key(local), "out", "📷 the camera", None)

    # --- bins: "📸 Check the bins", private and different per person -----------------

    def is_bin_admin(self, uid: int, role_ids, manages_server: bool) -> bool:
        """Who gets the detailed view. Listed users or roles; else server managers."""
        if self.bin_admins:
            return uid in self.bin_admins or bool(self.bin_admins & set(role_ids or ()))
        return manages_server

    async def async_inspect(self, *, admin: bool) -> tuple[Any, list[tuple[str, bytes]]]:
        """A fresh look, answered privately to whoever tapped "Check the bins".

        Everyone gets a read-only "here's what I think". Admins get the
        working: the snapshot with the region drawn, the crop, each bin's guess,
        confidence and closest sample, the lighting against the thresholds, and
        buttons to answer, which teach it like the card in the channel does.
        """
        if not self.cans_enabled:
            return status_view(
                "Bin watching is off.", [], GREY, "Pick a camera under Configure → Bin watcher.",
                controls=False,
            ), []
        if self._cans_lock.locked():
            return status_view("Already looking — tap again in a moment.", [], GREY, ""), []
        async with self._cans_lock:
            try:
                analysis, content, verdicts, daylight = await self._async_look()
            except HomeAssistantError as err:
                _LOGGER.warning("No snapshot from %s: %s", self.camera, err)
                return status_view("Couldn't get a snapshot from the camera.", [], RED, ""), []
            self._record(analysis, verdicts, daylight)
            local = dt_util.now()
            await self._async_resolve_seen(verdicts, local)
            diag = {
                "verdicts": verdicts,
                "elevation": self._sun_elevation(),
                "brightness": analysis.brightness,
                "daylight": daylight,
            }
            sid = None
            if admin and daylight:
                sid = self._new_sample(
                    analysis, verdicts, self.bins_tonight(local), trash.night_key(local), posted=False
                )
                self.state["cans"]["pending"][sid]["inspect"] = diag
                await self.hass.async_add_executor_job(self._write_sample, sid, analysis.crop_jpeg)
            self._changed()

        if not admin:
            lines = [
                f"{trash.BIN_EMOJI[b]} **{trash.BIN_NAME[b]}** · {self._plain_verdict(b, v)}"
                for b, v in verdicts.items()
            ]
            learning = [trash.BIN_NAME[b] for b in trash.BINS if not self.models[b].ready]
            footer = "Looked just now"
            if learning:
                footer += f" · still learning {' and '.join(learning).lower()}"
            return status_view("📸 The bins right now", lines, self._look_colour(verdicts), footer), []

        region = await self.hass.async_add_executor_job(vision.roi_preview, content, self.roi)
        view = self._inspect_card(sid, diag, "attachment://region.jpg", "attachment://crop.jpg")
        return view, [("region.jpg", region), ("crop.jpg", analysis.crop_jpeg)]

    def _plain_verdict(self, bin_name: str, v: dict) -> str:
        result = v["result"]
        if result in (trash.OUT, trash.NOT_OUT):
            return f"**{RESULT_TEXT[result]}** ({round(v['confidence'] * 100)}% sure)"
        if result == trash.UNSURE and v.get("label"):
            word = "out" if v["label"] == vision.LABEL_OUT else "not out"
            return f"not sure, maybe {word}"
        return RESULT_TEXT[result]

    @staticmethod
    def _look_colour(verdicts: dict) -> int:
        results = {v["result"] for v in verdicts.values()}
        if results == {trash.OUT}:
            return GREEN
        if trash.DARK in results:
            return GREY
        return BLUE if trash.UNSURE in results else YELLOW

    def inspect_layout(self, sid: str, region: str, crop: str):
        """Re-render an admin's private look after they answer on it."""
        sample = self.state["cans"]["pending"].get(sid)
        if sample is None or "inspect" not in sample:
            return None
        return self._inspect_card(sid, sample["inspect"], region, crop)

    def _inspect_card(self, sid: str | None, diag: dict, region: str, crop: str):
        sample = self.state["cans"]["pending"].get(sid) if sid else None
        answers = sample["answers"] if sample else {}
        rows: list[BinRow] = []
        details: list[str] = []
        for b, v in diag["verdicts"].items():
            model = self.models[b]
            answer = answers.get(b)
            if answer:
                word = "out" if answer["label"] == vision.LABEL_OUT else "not out"
                mark = "" if answer.get("right") is None else (" ✓ guess right" if answer["right"] else " ✗ guess wrong")
                text = f"**{word}** says {answer['who']}{mark}"
            else:
                text = f"**{RESULT_TEXT[v['result']]}**"
            rows.append(BinRow(b, trash.BIN_EMOJI[b], trash.BIN_NAME[b], text, bool(answer)))
            guess = (
                "no samples yet" if v.get("label") is None
                else f"guess {'out' if v['label'] == vision.LABEL_OUT else 'not out'} "
                f"{round(v['confidence'] * 100)}% · closest {v['nearest']:.2f} · compared {v['compared']}"
            )
            trust = "trusted" if model.ready else "learning"
            acc = "—" if model.accuracy is None else f"{round(model.accuracy * 100)}%"
            details.append(
                f"{trash.BIN_EMOJI[b]} {guess}\n-# {trust} · {model.out} out · {model.not_out} not out · "
                f"self-test {acc} (trusts at {self.min_labels} each and {round(self.min_accuracy * 100)}%)"
            )
        elevation = diag.get("elevation")
        sun = "sun ?" if elevation is None else f"sun {elevation:.0f}° (needs {self.min_elevation:g}°)"
        light = (
            f"{'☀️' if diag['daylight'] else '🌙'} {sun} · brightness {diag['brightness']:.0f} "
            f"(needs {self.min_brightness:g}) · {'judging' if diag['daylight'] else 'too dark, not judging'}"
        )
        if sid is None:
            note = "Too dark to answer this one; it wouldn't teach anything."
        elif sample and sample.get("skipped_by"):
            note = f"🤷 {sample['skipped_by']} couldn't tell; not used for learning."
        else:
            note = "Answering teaches it, same as the card in the channel."
        return inspect_view(
            InspectCard(
                sid=sid,
                colour=self._look_colour(diag["verdicts"]),
                region=region,
                crop=crop,
                rows=rows,
                details=details,
                light=light,
                note=note,
                closed=sid is None or bool(sample and sample.get("closed")),
            )
        )

    def _guess_text(self, bin_name: str, guess: dict) -> str:
        label = guess.get("label")
        if label is None:
            return "no guess yet"
        word = "out" if label == vision.LABEL_OUT else "not out"
        hedge = "" if self.models[bin_name].ready else ", learning"
        return f"I think **{word}** ({round(guess.get('confidence', 0) * 100)}%{hedge})"

    def _new_sample(
        self, analysis: vision.Analysis, verdicts: dict, tonight: list[str], key: str, *, posted: bool
    ) -> str:
        sid = trash.new_sample_id()
        cans = self.state["cans"]
        cans["pending"][sid] = {
            "at": time_mod.time(),
            "roi": self.roi_key,
            "features": analysis.features,
            "guesses": {b: {"label": v["label"], "confidence": v["confidence"]} for b, v in verdicts.items()},
            "answers": {},
            "skipped_by": None,
            "nights": {b: (key if b in tonight else None) for b in trash.BINS},
            "message_id": None,
            "posted": posted,
            "closed": False,
        }
        cans["pending"] = trash.prune_pending(cans["pending"], time_mod.time())
        return sid

    async def _async_ask(
        self, analysis: vision.Analysis, verdicts: dict, tonight: list[str], key: str
    ) -> bool:
        sid = self._new_sample(analysis, verdicts, tonight, key, posted=True)
        await self.hass.async_add_executor_job(self._write_sample, sid, analysis.crop_jpeg)
        try:
            message_id = await self._bot.async_post(
                self.cans_channel_id,
                self.ask_layout(sid, "attachment://cans.jpg"),
                files=[("cans.jpg", analysis.crop_jpeg)],
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not post the bin snapshot", exc_info=True)
            return False
        self.state["cans"]["pending"][sid]["message_id"] = message_id
        return True

    def ask_layout(self, sid: str, image: str):
        sample = self.state["cans"]["pending"].get(sid)
        if sample is None:
            return None
        rows = []
        for b in trash.BINS:
            answer = sample["answers"].get(b)
            if answer:
                word = "out" if answer["label"] == vision.LABEL_OUT else "not out"
                mark = ""
                if answer.get("right") is not None:
                    mark = " · my guess was right ✓" if answer["right"] else " · my guess was wrong ✗"
                text = f"**{word}** says {answer['who']}{mark}"
            elif sample.get("skipped_by"):
                text = "skipped"
            else:
                text = self._guess_text(b, sample["guesses"].get(b) or {})
            rows.append(BinRow(b, trash.BIN_EMOJI[b], trash.BIN_NAME[b], text, bool(answer)))
        note = f"🤷 {sample['skipped_by']} couldn't tell; not used for learning." if sample.get("skipped_by") else None
        closed = sample.get("closed", False)
        return ask_view(
            AskCard(
                sid=sid,
                colour=(GREY if sample.get("skipped_by") else GREEN) if closed else BLUE,
                title="📸 Bins at the curb" if closed else "📸 Are the bins out?",
                image=image,
                rows=rows,
                footer=self._summary(),
                closed=closed,
                note=note,
            )
        )

    # --- bins: labels ---------------------------------------------------------------

    async def async_label(
        self, sid: str, bin_name: str, label: str, who: str, uid: int | None,
        *, edit_message: bool = True,
    ) -> str | None:
        """Record an answer for one bin (or ``skip`` for the whole snapshot)."""
        cans = self.state["cans"]
        sample = cans["pending"].get(sid)
        if sample is None or sample.get("closed"):
            return None
        if label == "skip":
            sample["closed"] = True
            sample["skipped_by"] = who
            reply = "Skipped — blurry or blocked shots are better left out."
        else:
            if bin_name not in trash.BINS or bin_name in sample["answers"]:
                return None
            guess = (sample["guesses"].get(bin_name) or {}).get("label")
            sample["answers"][bin_name] = {
                "label": label, "who": who, "right": None if guess is None else guess == label,
            }
            cans["refs"] = trash.add_reference(
                cans["refs"],
                {"sid": sid, "bin": bin_name, "label": label, "roi": sample["roi"],
                 "features": sample["features"], "at": time_mod.time(), "by": who},
            )
            await self._async_refresh_models([bin_name])
            if all(b in sample["answers"] for b in trash.BINS):
                sample["closed"] = True
            model = self.models[bin_name]
            right = sample["answers"][bin_name]["right"]
            verdict = "" if right is None else (" My guess was right ✓." if right else " My guess was wrong ✗.")
            needed = "" if model.ready else (
                f" I trust myself at {self.min_labels} of each and "
                f"{round(self.min_accuracy * 100)}% on the self-test."
            )
            reply = f"Thanks, learned.{verdict} {trash.BIN_NAME[bin_name]}: {model.summary()}.{needed}"
            night = sample["nights"].get(bin_name)
            if label == vision.LABEL_OUT and night:
                await self._async_resolve(bin_name, night, "out", who, uid)
        self._changed()
        await self.hass.async_add_executor_job(self._cleanup_files)
        if edit_message and sample.get("message_id"):
            try:
                await self._bot.async_edit_keeping(
                    self.cans_channel_id, sample["message_id"],
                    lambda url: self.ask_layout(sid, url),
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not update the snapshot card", exc_info=True)
        return reply

    # --- bins: the night reminder -------------------------------------------------

    def _night(self, bin_name: str, key: str) -> dict:
        nights = self.state["cans"]["nights"]
        night = nights.get(bin_name)
        if not isinstance(night, dict) or night.get("key") != key:
            night = {"key": key, "resolved": None, "by": None, "chore_marked": False}
            nights[bin_name] = night
        return night

    async def _tick_night(self, _now) -> None:
        try:
            await self.async_cans_night()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Bin-night reminder failed")

    def _mention(self, bins: list[str]) -> str:
        parts = [self.nag_mention] if self.nag_mention else []
        now = time_mod.time()
        for b in bins:
            chore = self.find_chore(self.bin_chore[b]) if self.bin_chore[b] != C.NO_CHORE else None
            if chore:
                item = chores_mod.entry(self.state["chores"], chore.key)
                if chores_mod.claimant(item, now) and item.get("claimed_by_id"):
                    mention = f"<@{item['claimed_by_id']}>"
                    if mention not in parts:
                        parts.append(mention)
        return " ".join(parts)

    def _open_bins(self, key: str, bins: list[str]) -> list[str]:
        return [b for b in bins if not self._night(b, key)["resolved"]]

    async def async_cans_night(self, *, bins: list[str] | None = None) -> None:
        local = dt_util.now()
        bins = bins if bins is not None else self.bins_tonight(local)
        if not bins:
            return
        key = trash.night_key(local)
        if not self._open_bins(key, bins):
            return
        # Take one fresh look first, without asking (the reminder asks instead).
        await self.async_check_cans(manual=False, allow_ask=False)
        open_bins = self._open_bins(key, bins)
        if not open_bins:
            return  # the fresh look saw them all out
        last = self.state["cans"].get("last") or {}
        plans = {}
        for b in open_bins:
            plan = trash.plan_nag(
                ready=self.models[b].ready,
                last_result=(last.get("result") or {}).get(b),
                last_at=last.get("at"),
                now=time_mod.time(),
            )
            if plan.kind == "none":
                await self._async_resolve(b, key, "out", "📷 the camera", None)
            else:
                plans[b] = plan.kind
        if not plans:
            return
        await self._async_post_nag(key, bins, plans, first=True)
        renag = int(self._opts.get(C.CONF_RENAG, C.DEFAULT_RENAG))
        if renag > 0:
            self._unsubs.append(async_call_later(self.hass, renag * 60, self._renag_later(key)))

    def _renag_later(self, key: str):
        async def _run(_now) -> None:
            try:
                nag = self.state["cans"].get("nag") or {}
                if nag.get("key") != key or nag.get("renagged"):
                    return
                nag["renagged"] = True
                bins = nag.get("bins") or []
                if not self._open_bins(key, bins):
                    return
                await self.async_check_cans(manual=False, allow_ask=False)
                open_bins = self._open_bins(key, bins)
                if open_bins:
                    await self._async_post_nag(key, bins, nag.get("plans") or {}, first=False)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Second bin reminder failed")

        return _run

    async def _async_post_nag(self, key: str, bins: list[str], plans: dict, *, first: bool) -> None:
        recent = self._last_crop if (
            self._last_crop and time_mod.time() - self._last_crop[0] < _CROP_REUSE_S
        ) else None
        last = self.state["cans"].get("last") or {}
        sid = None
        if recent is not None and last.get("features") and "ask" in plans.values():
            # Answering also labels the picture.
            analysis = vision.Analysis(last["features"], last.get("brightness", 0), recent[1])
            verdicts = {b: {"label": (last["guess"].get(b) or {}).get("label"),
                            "confidence": (last["guess"].get(b) or {}).get("confidence", 0)}
                        for b in trash.BINS}
            sid = self._new_sample(analysis, verdicts, bins, key, posted=True)
            await self.hass.async_add_executor_job(self._write_sample, sid, recent[1])
        self.state["cans"]["nag"] = {
            "key": key, "bins": bins, "plans": plans, "message_id": None,
            "renagged": not first, "has_image": recent is not None, "sid": sid, "repeat": not first,
        }
        mention = self._mention(self._open_bins(key, bins))
        try:
            message_id = await self._bot.async_post(
                self.cans_channel_id,
                self.nag_layout(key, "attachment://cans.jpg", mention=mention),
                files=[("cans.jpg", recent[1])] if recent else None,
                silent=False,
                mention=bool(mention),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not post the bin reminder", exc_info=True)
            return
        self.state["cans"]["nag"]["message_id"] = message_id
        if sid:
            self.state["cans"]["pending"][sid]["message_id"] = None  # answered via the nag
        self._changed()

    def nag_layout(self, key: str, image: str | None, *, mention: str | None = None):
        nag = self.state["cans"].get("nag") or {}
        if nag.get("key") != key:
            return None
        bins = nag["bins"]
        plans = nag.get("plans") or {}
        lines = []
        open_bins = []
        for b in bins:
            night = self._night(b, key)
            label = f"{trash.BIN_EMOJI[b]} **{trash.BIN_NAME[b]}**"
            if night["resolved"] == "out":
                lines.append(f"{label} · ✅ out ({night['by']})")
            elif night["resolved"] == "skip":
                lines.append(f"{label} · ⏭️ no pickup this week ({night['by']})")
            elif plans.get(b) == "nag":
                lines.append(f"{label} · not out yet — the camera doesn't see it")
                open_bins.append((b, trash.BIN_EMOJI[b], trash.BIN_NAME[b]))
            else:
                hedge = "I'm still learning, so tell me" if not self.models[b].ready else "I can't tell from here"
                lines.append(f"{label} · is it out? {hedge}")
                open_bins.append((b, trash.BIN_EMOJI[b], trash.BIN_NAME[b]))
        names = " & ".join(f"{trash.BIN_EMOJI[b]} {trash.BIN_NAME[b]}" for b in bins)
        title = f"🌙 Bin night: {names}"
        if nag.get("repeat"):
            title = "🔁 " + title
        if not open_bins:
            colour = GREEN
        elif any(plans.get(b) == "nag" for b, _e, _n in open_bins):
            colour = ORANGE
        else:
            colour = BLUE
        return nag_view(
            NagCard(
                night=key,
                colour=colour,
                mention=(mention if mention is not None else self._mention([b for b, _e, _n in open_bins]))
                if open_bins else "",
                title=title,
                lines=lines,
                image=image if nag.get("has_image") else None,
                open_bins=open_bins,
                footer=self._summary(),
            )
        )

    async def async_nag_action(
        self, key: str, bin_name: str, action: str, who: str, uid: int | None
    ) -> bool:
        nag = self.state["cans"].get("nag") or {}
        if nag.get("key") != key:
            return False
        if action == "skip":
            for b in self._open_bins(key, nag["bins"]):
                await self._async_resolve(b, key, "skip", who, uid, edit=False)
        else:
            sid = nag.get("sid")
            if sid and sid in self.state["cans"]["pending"]:
                await self.async_label(sid, bin_name, "out", who, uid, edit_message=False)
            await self._async_resolve(bin_name, key, "out", who, uid, edit=False)
        self._changed()
        return True

    async def _async_resolve(
        self, bin_name: str, key: str, how: str, who: str, uid: int | None, *, edit: bool = True
    ) -> None:
        night = self._night(bin_name, key)
        if night["resolved"]:
            return
        night["resolved"] = how
        night["by"] = who
        chore_key = self.bin_chore[bin_name]
        if how == "out" and not night["chore_marked"] and chore_key != C.NO_CHORE:
            night["chore_marked"] = True
            await self.async_mark_done(chore_key, who, uid, refresh=False)
        nag = self.state["cans"].get("nag") or {}
        if edit and nag.get("key") == key and nag.get("message_id"):
            try:
                await self._bot.async_edit_keeping(
                    self.cans_channel_id, nag["message_id"], lambda url: self.nag_layout(key, url)
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not update the night reminder", exc_info=True)
        self._changed()
        await self.async_update_board()

    # --- setup helpers ----------------------------------------------------------------

    async def async_preview_region(self) -> str:
        if not self.camera:
            return "No camera configured."
        try:
            content = await _snapshot(self.hass, self.camera)
        except HomeAssistantError as err:
            return f"Couldn't get a snapshot: {err}"
        preview = await self.hass.async_add_executor_job(vision.roi_preview, content, self.roi)
        analysis = await self.hass.async_add_executor_job(vision.analyse, content, self.roi)
        text = (
            "### 🟥 Where I look for the bins\n"
            "The red box is the only part of the picture I compare. Make it snug "
            "around where the bins stand at the curb, with a little margin. The "
            "easiest way is to drag it on the **Chores** page in Home Assistant's "
            "sidebar. Changing it starts the learning over."
        )
        await self._bot.async_post(
            self.cans_channel_id,
            preview_view(text, BLUE),
            files=[("region.jpg", preview), ("crop.jpg", analysis.crop_jpeg)],
        )
        return "Posted."

    async def async_reset_cans(self) -> None:
        self.state["cans"] = _blank_state()["cans"]
        await self._async_refresh_models()
        self._changed()
        await self.hass.async_add_executor_job(self._cleanup_files)
        await self.async_update_board()

    # --- training page ------------------------------------------------------------

    async def async_panel_overview(self) -> dict:
        cans = self.state["cans"]
        now = time_mod.time()
        return {
            "camera": self.camera,
            "enabled": self.cans_enabled,
            "roi": list(self.roi_percent),
            "thresholds": {
                "min_labels": self.min_labels,
                "min_accuracy": self.min_accuracy,
                "confidence": self.confidence,
                "min_elevation": self.min_elevation,
                "min_brightness": self.min_brightness,
            },
            "bins": {
                b: {
                    "name": trash.BIN_NAME[b],
                    "emoji": trash.BIN_EMOJI[b],
                    "out": self.models[b].out,
                    "not_out": self.models[b].not_out,
                    "accuracy": self.models[b].accuracy,
                    "ready": self.models[b].ready,
                    "phase": self.models[b].phase,
                    "nights": [C.WEEKDAYS[d] for d in sorted(self.nights[b])],
                    "weeks": self.weeks[b],
                    "set_aside": len(trash.for_bin(cans["refs"], b)) - len(self._refs(b)),
                }
                for b in trash.BINS
            },
            "last": {k: v for k, v in (cans.get("last") or {}).items() if k != "features"} or None,
            "pending": [
                {"sid": sid, "at": s["at"], "guesses": s["guesses"], "answers": s["answers"],
                 "age": now - s["at"]}
                for sid, s in sorted(cans["pending"].items(), key=lambda kv: -kv[1]["at"])
                if not s.get("closed")
            ],
        }

    async def async_panel_snapshot(self) -> dict:
        """A fresh look for the training page; labelable there, posted nowhere."""
        if not self.camera:
            raise HomeAssistantError("No camera configured")
        async with self._cans_lock:
            analysis, content, verdicts, daylight = await self._async_look()
            self._record(analysis, verdicts, daylight)
        frame = await self.hass.async_add_executor_job(vision.frame, content)
        local = dt_util.now()
        sid = None
        if daylight:
            sid = self._new_sample(analysis, verdicts, self.bins_tonight(local), trash.night_key(local), posted=False)
            await self.hass.async_add_executor_job(self._write_sample, sid, analysis.crop_jpeg)
        self._changed()
        return {
            "sid": sid,
            "frame": _b64(frame),
            "crop": _b64(analysis.crop_jpeg),
            "roi": list(self.roi_percent),
            "brightness": analysis.brightness,
            "elevation": self._sun_elevation(),
            "daylight": daylight,
            "verdicts": verdicts,
        }

    async def async_panel_samples(self) -> list[dict]:
        refs = self.state["cans"]["refs"]

        def build() -> list[dict]:
            out = []
            for b in trash.BINS:
                usable = self._refs(b)
                details = vision.loo_details(usable)
                by_obj = {id(r): details.get(i) for i, r in enumerate(usable)}
                for ref in trash.for_bin(refs, b):
                    pred = by_obj.get(id(ref))
                    path = self._sample_path(ref["sid"])
                    thumb = None
                    if os.path.exists(path):
                        with open(path, "rb") as handle:
                            thumb = vision.thumbnail(handle.read())
                    out.append({
                        "id": f"{ref['sid']}:{b}",
                        "sid": ref["sid"],
                        "bin": b,
                        "label": ref["label"],
                        "at": ref.get("at"),
                        "by": ref.get("by"),
                        "current_region": vision.usable(ref, self.roi_key),
                        "model_says": pred.label if pred else None,
                        "model_confidence": pred.confidence if pred else None,
                        "thumb": _b64(thumb),
                    })
            out.sort(key=lambda r: -(r["at"] or 0))
            return out

        return await self.hass.async_add_executor_job(build)

    def _find_ref(self, ref_id: str) -> dict | None:
        sid, _, b = ref_id.partition(":")
        for ref in self.state["cans"]["refs"]:
            if ref.get("sid") == sid and trash.ref_bin(ref) == b:
                return ref
        return None

    async def async_panel_relabel(self, ref_id: str, label: str) -> bool:
        ref = self._find_ref(ref_id)
        if ref is None or label not in vision.LABELS:
            return False
        ref["label"] = label
        await self._async_refresh_models([trash.ref_bin(ref)])
        self._changed()
        await self.async_update_board()
        return True

    async def async_panel_delete(self, ref_id: str) -> bool:
        ref = self._find_ref(ref_id)
        if ref is None:
            return False
        self.state["cans"]["refs"].remove(ref)
        await self._async_refresh_models([trash.ref_bin(ref)])
        self._changed()
        await self.hass.async_add_executor_job(self._cleanup_files)
        await self.async_update_board()
        return True

    async def async_panel_set_region(self, roi: list[float]) -> None:
        left, top, right, bottom = (max(0.0, min(100.0, float(v))) for v in roi)
        options = {**self.entry.options}
        options.update({
            C.CONF_ROI_LEFT: round(min(left, right), 1),
            C.CONF_ROI_TOP: round(min(top, bottom), 1),
            C.CONF_ROI_RIGHT: round(max(left, right), 1),
            C.CONF_ROI_BOTTOM: round(max(top, bottom), 1),
        })
        # Saving options reloads the integration with the new region.
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    # --- sample files: /config/chores_discord/samples/<sid>.jpg ------------------------

    def _sample_path(self, sid: str) -> str:
        return self.hass.config.path(C.SAMPLES_DIR, "samples", f"{sid}.jpg")

    def _write_sample(self, sid: str, data: bytes) -> None:
        path = self._sample_path(sid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)

    def _cleanup_files(self) -> None:
        """Delete crops no reference or open sample points at any more."""
        folder = os.path.dirname(self._sample_path("x"))
        if not os.path.isdir(folder):
            return
        cans = self.state["cans"]
        keep = {r.get("sid") for r in cans["refs"]}
        keep |= {sid for sid, s in cans["pending"].items() if not s.get("closed")}
        for name in os.listdir(folder):
            if name.endswith(".jpg") and name[:-4] not in keep:
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass

    # --- for entities -------------------------------------------------------------

    def bin_state(self, bin_name: str) -> str:
        last = self.state["cans"].get("last") or {}
        return (last.get("result") or {}).get(bin_name, trash.UNKNOWN)

    def bin_attributes(self, bin_name: str) -> dict:
        last = self.state["cans"].get("last") or {}
        guess = (last.get("guess") or {}).get(bin_name) or {}
        model = self.models[bin_name]
        return {
            "checked_at": dt_util.utc_from_timestamp(last["at"]).isoformat() if last.get("at") else None,
            "guess": guess.get("label"),
            "confidence": guess.get("confidence"),
            "brightness": last.get("brightness"),
            "labels_out": model.out,
            "labels_not_out": model.not_out,
            "self_test_accuracy": model.accuracy,
            "phase": model.phase,
            "trusted": model.ready,
        }
