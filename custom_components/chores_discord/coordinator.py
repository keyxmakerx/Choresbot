"""State, timers and the glue between Home Assistant, the rules and Discord."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import os
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

from . import chores as chores_mod, trash, vision
from .const import (
    CONF_ASK_GAP,
    CONF_ASKS_PER_DAY,
    CONF_BOT_TOKEN,
    CONF_CAMERA,
    CONF_CANS_CHANNEL_ID,
    CONF_CANS_CHORE,
    CONF_CANS_ENABLED,
    CONF_CANS_NIGHTS,
    CONF_CHANNEL_ID,
    CONF_CHECK_INTERVAL,
    CONF_CONFIDENCE,
    CONF_CUSTOM_CHORES,
    CONF_DIGEST,
    CONF_DIGEST_TIME,
    CONF_DISABLED_CHORES,
    CONF_MIN_ACCURACY,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_ELEVATION,
    CONF_MIN_LABELS,
    CONF_NAG_MENTION,
    CONF_NAG_TIME,
    CONF_RENAG,
    CONF_ROI_BOTTOM,
    CONF_ROI_LEFT,
    CONF_ROI_RIGHT,
    CONF_ROI_TOP,
    CONF_SAVE_SAMPLES,
    DEFAULT_ASK_GAP,
    DEFAULT_ASKS_PER_DAY,
    DEFAULT_CANS_CHORE,
    DEFAULT_CANS_ENABLED,
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_CONFIDENCE,
    DEFAULT_CUSTOM_CHORES,
    DEFAULT_DIGEST,
    DEFAULT_DIGEST_TIME,
    DEFAULT_MIN_ACCURACY,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_MIN_ELEVATION,
    DEFAULT_MIN_LABELS,
    DEFAULT_NAG_TIME,
    DEFAULT_RENAG,
    DEFAULT_ROI,
    DEFAULT_SAVE_SAMPLES,
    NO_CHORE,
    SAMPLES_DIR,
    SAMPLES_KEEP,
    SIGNAL_UPDATE,
    STORAGE_KEY,
    STORAGE_VERSION,
    WEEKDAYS,
)
from .discord_bot import BoardView, DiscordBot, label_view, nag_view

_LOGGER = logging.getLogger(__name__)

type ChoresConfigEntry = ConfigEntry["ChoresCoordinator"]

_COLOUR_BOARD = 0x5865F2
_COLOUR_OK = 0x2ECC71
_COLOUR_WARN = 0xE67E22
_COLOUR_ASK = 0x3498DB
_COLOUR_DONE = 0x95A5A6

# A snapshot this recent can be reused for the night reminder.
_CROP_REUSE_S = 15 * 60
_CAMERA_TIMEOUT = 15

_RESULT_TEXT = {
    trash.OUT: "out",
    trash.NOT_OUT: "not out",
    trash.UNSURE: "not sure",
    trash.DARK: "too dark to see",
    trash.UNKNOWN: "not checked yet",
}


def _blank_state() -> dict:
    return {
        "chores": {},
        "board": {"message_id": None},
        "cans": {"refs": [], "pending": {}, "last": None, "asks": [], "night": None},
    }


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
        self.channel_id = int(opts[CONF_CHANNEL_ID])
        cans_channel = str(opts.get(CONF_CANS_CHANNEL_ID) or "").strip()
        self.cans_channel_id = int(cans_channel) if cans_channel.isdigit() else self.channel_id

        self.chores = chores_mod.catalogue(
            opts.get(CONF_CUSTOM_CHORES, DEFAULT_CUSTOM_CHORES),
            list(opts.get(CONF_DISABLED_CHORES) or []),
        )
        self.camera = opts.get(CONF_CAMERA) or ""
        self.cans_enabled = bool(opts.get(CONF_CANS_ENABLED, DEFAULT_CANS_ENABLED)) and bool(
            self.camera
        )
        self.roi = vision.clamp_roi(
            tuple(
                float(opts.get(key, default)) / 100.0
                for key, default in zip(
                    (CONF_ROI_LEFT, CONF_ROI_TOP, CONF_ROI_RIGHT, CONF_ROI_BOTTOM), DEFAULT_ROI
                )
            )
        )
        self.roi_key = vision.roi_key(self.roi)
        self.nights = [int(d) for d in opts.get(CONF_CANS_NIGHTS) or []]
        self.cans_chore = opts.get(CONF_CANS_CHORE, DEFAULT_CANS_CHORE) or NO_CHORE
        self.nag_mention = str(opts.get(CONF_NAG_MENTION) or "").strip()
        self.confidence = float(opts.get(CONF_CONFIDENCE, DEFAULT_CONFIDENCE)) / 100.0
        self.min_labels = int(opts.get(CONF_MIN_LABELS, DEFAULT_MIN_LABELS))
        self.min_accuracy = float(opts.get(CONF_MIN_ACCURACY, DEFAULT_MIN_ACCURACY)) / 100.0

        self.state: dict[str, Any] = _blank_state()
        self.model = trash.model_status({}, None, self.min_labels, self.min_accuracy)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")
        self._bot = DiscordBot(self, opts[CONF_BOT_TOKEN])
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
        await self._async_refresh_model()

        self._unsubs.append(
            async_track_time_interval(self.hass, self._tick_board, timedelta(hours=1))
        )
        digest = trash.parse_time(self._opts.get(CONF_DIGEST_TIME, DEFAULT_DIGEST_TIME), None)
        if digest and self._opts.get(CONF_DIGEST, DEFAULT_DIGEST):
            self._unsubs.append(
                async_track_time_change(
                    self.hass, self._tick_digest, hour=digest.hour, minute=digest.minute, second=0
                )
            )
        if self.cans_enabled:
            minutes = int(self._opts.get(CONF_CHECK_INTERVAL, DEFAULT_CHECK_INTERVAL))
            self._unsubs.append(
                async_track_time_interval(
                    self.hass, self._tick_cans, timedelta(minutes=max(5, minutes))
                )
            )
            if self.nights:
                nag = trash.parse_time(self._opts.get(CONF_NAG_TIME, DEFAULT_NAG_TIME), None)
                if nag:
                    self._unsubs.append(
                        async_track_time_change(
                            self.hass, self._tick_night, hour=nag.hour, minute=nag.minute, second=0
                        )
                    )

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
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry.entry_id}")

    # --- chores ----------------------------------------------------------------

    def find_chore(self, key_or_name: str) -> chores_mod.Chore | None:
        wanted = chores_mod.slugify(key_or_name)
        for chore in self.chores:
            if chore.key == key_or_name or chore.key == wanted:
                return chore
        return None

    async def async_mark_done(
        self, key: str, who: str, uid: int | None, *, refresh: bool = True
    ) -> chores_mod.Chore | None:
        chore = self.find_chore(key)
        if chore is None:
            return None
        chores_mod.mark_done(self.state["chores"], chore.key, time_mod.time(), who, uid)
        self._save()
        self._signal()
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
        self._save()
        self._signal()
        return chore, claimed

    async def async_undo(self, key: str, uid: int | None) -> bool:
        ok = chores_mod.undo(self.state["chores"], key, uid)
        if ok:
            self._save()
            self._signal()
            await self.async_update_board()
        return ok

    def overdue(self) -> list[chores_mod.Chore]:
        return chores_mod.overdue(self.chores, self.state["chores"], time_mod.time())

    def board_embed(self) -> discord.Embed:
        now = time_mod.time()
        lines = chores_mod.board_lines(self.chores, self.state["chores"], now)
        embed = discord.Embed(
            title="🧹 Chores",
            description="\n".join(lines) or "No chores configured.",
            colour=_COLOUR_BOARD,
            timestamp=dt_util.utcnow(),
        )
        if self.cans_enabled:
            last = self.state["cans"].get("last") or {}
            result = last.get("result", trash.UNKNOWN)
            when = ""
            if last.get("at"):
                when = f" ({chores_mod.ago(now - last['at'])} ago)".replace(
                    "(just now ago)", "(just now)"
                )
            nights = ", ".join(WEEKDAYS[d] for d in sorted(self.nights)) or "not set"
            embed.add_field(
                name="🗑️ Trash cans",
                value=(
                    f"Last look: **{_RESULT_TEXT.get(result, result)}**{when}\n"
                    f"Cans night: {nights}\n"
                    f"Learning: {self.model.summary()}"
                ),
                inline=False,
            )
        embed.set_footer(text="Pick from the menus to mark a chore done or claim it.")
        return embed

    async def async_update_board(self, *, repost: bool = False) -> None:
        """Edit the board in place, or post it if it's missing (or ``repost``)."""
        if not self._bot.is_ready:
            return
        async with self._board_lock:
            board = self.state["board"]
            message_id = board.get("message_id")
            if message_id and not repost:
                try:
                    await self._bot.async_edit(
                        self.channel_id, message_id, embed=self.board_embed(), view=BoardView(self)
                    )
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
                    self.channel_id, embed=self.board_embed(), view=BoardView(self)
                )
                self._save()
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Could not post the chore board", exc_info=True)

    async def _tick_board(self, _now) -> None:
        await self.async_update_board()

    async def _tick_digest(self, _now) -> None:
        """Daily: move the board to the bottom of the channel (silently)."""
        await self.async_update_board(repost=True)

    # --- trash cans: model -----------------------------------------------------

    def _usable_refs(self) -> list[dict]:
        return [r for r in self.state["cans"]["refs"] if vision.usable(r, self.roi_key)]

    async def _async_refresh_model(self) -> None:
        refs = self._usable_refs()
        accuracy = await self.hass.async_add_executor_job(vision.leave_one_out, refs)
        self.model = trash.model_status(
            trash.label_counts(refs), accuracy, self.min_labels, self.min_accuracy
        )

    def _sun_elevation(self) -> float | None:
        sun = self.hass.states.get("sun.sun")
        if sun is None:
            return None
        try:
            return float(sun.attributes.get("elevation"))
        except (TypeError, ValueError):
            return None

    # --- trash cans: checking --------------------------------------------------

    async def _tick_cans(self, _now) -> None:
        try:
            await self.async_check_cans(manual=False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Scheduled can check failed")

    async def async_check_cans(self, *, manual: bool, allow_ask: bool = True) -> str:
        """Take a snapshot, classify it, maybe ask for a label. Returns a summary."""
        if not self.cans_enabled:
            return "Can watching is off. Turn it on under Configure and pick the camera."
        elevation = self._sun_elevation()
        min_elevation = float(self._opts.get(CONF_MIN_ELEVATION, DEFAULT_MIN_ELEVATION))
        if not manual and elevation is not None and elevation < min_elevation:
            return "Sun's down; not looking."
        if self._cans_lock.locked():
            return "Already checking — one moment."
        async with self._cans_lock:
            try:
                content = await _snapshot(self.hass, self.camera)
            except HomeAssistantError as err:
                _LOGGER.warning("No snapshot from %s: %s", self.camera, err)
                return f"Couldn't get a snapshot from {self.camera}."
            analysis = await self.hass.async_add_executor_job(
                vision.analyse, content, self.roi
            )
            now = time_mod.time()
            cans = self.state["cans"]
            daylight = trash.is_daylight(
                elevation,
                analysis.brightness,
                min_elevation,
                float(self._opts.get(CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS)),
            )
            if not daylight:
                cans["last"] = {"at": now, "result": trash.DARK, "brightness": analysis.brightness}
                self._save()
                self._signal()
                return "Too dark to see the cans right now. I only judge them in daylight."

            refs = self._usable_refs()
            prediction = await self.hass.async_add_executor_job(
                vision.classify, refs, analysis.features
            )
            result = (
                trash.verdict(prediction.label, prediction.confidence, self.confidence)
                if self.model.ready
                else trash.UNSURE
            )
            cans["last"] = {
                "at": now,
                "result": result,
                "guess": prediction.label,
                "confidence": prediction.confidence,
                "brightness": analysis.brightness,
                # Kept so the night reminder can turn this look into a label.
                "features": analysis.features,
            }
            self._last_crop = (now, analysis.crop_jpeg)
            local = dt_util.now()
            cans_night = trash.is_cans_night(local, self.nights)

            asked = False
            if allow_ask and (
                manual
                or trash.should_ask(
                    now=now,
                    result=result,
                    ready=self.model.ready,
                    asks=cans["asks"],
                    max_per_day=int(self._opts.get(CONF_ASKS_PER_DAY, DEFAULT_ASKS_PER_DAY)),
                    min_gap_hours=float(self._opts.get(CONF_ASK_GAP, DEFAULT_ASK_GAP)),
                    cans_night=cans_night,
                )
            ):
                await self._async_ask(analysis, prediction, night=trash.night_key(local) if cans_night else None)
                if not manual:
                    cans["asks"] = [t for t in cans["asks"] if now - t < trash.DAY] + [now]
                asked = True

            if cans_night and result == trash.OUT:
                await self._async_resolve_night(trash.night_key(local), "out", "📷 the camera", None)

            self._save()
            self._signal()

        text = self._guess_line(prediction)
        if asked:
            text += " Posted the snapshot — tap what you see."
        return text

    def _guess_line(self, prediction: vision.Prediction) -> str:
        if prediction.label is None:
            return "No labels yet, so no guess. Every answer teaches me."
        word = "out" if prediction.label == vision.LABEL_OUT else "not out"
        hedge = "" if self.model.ready else " (still learning, so I won't act on it)"
        return f"My guess: **{word}**, {round(prediction.confidence * 100)}% sure{hedge}."

    async def _async_ask(
        self, analysis: vision.Analysis, prediction: vision.Prediction, *, night: str | None
    ) -> None:
        sid = trash.new_sample_id()
        embed = discord.Embed(
            title="📸 Are the trash cans out?",
            description=self._guess_line(prediction),
            colour=_COLOUR_ASK,
            timestamp=dt_util.utcnow(),
        )
        embed.set_image(url="attachment://cans.jpg")
        embed.set_footer(text=f"Learning: {self.model.summary()}")
        view = nag_view(night, with_labels_sid=sid) if night else label_view(sid)
        try:
            message_id = await self._bot.async_post(
                self.cans_channel_id,
                embed=embed,
                view=view,
                files=[("cans.jpg", analysis.crop_jpeg)],
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not post the can snapshot", exc_info=True)
            return
        cans = self.state["cans"]
        cans["pending"][sid] = {
            "at": time_mod.time(),
            "roi": self.roi_key,
            "features": analysis.features,
            "guess": prediction.label,
            "confidence": prediction.confidence,
            "night": night,
            "message_id": message_id,
        }
        cans["pending"] = trash.prune_pending(cans["pending"], time_mod.time())
        if self._opts.get(CONF_SAVE_SAMPLES, DEFAULT_SAVE_SAMPLES):
            await self.hass.async_add_executor_job(
                self._write_sample, "pending", sid, analysis.crop_jpeg
            )

    # --- trash cans: labels ----------------------------------------------------

    async def async_label(
        self, sid: str, label: str, who: str, uid: int | None, base: discord.Embed | None
    ) -> tuple[discord.Embed, str] | None:
        cans = self.state["cans"]
        sample = cans["pending"].pop(sid, None)
        if sample is None:
            return None
        embed = base.copy() if base is not None else discord.Embed()
        embed.title = "📸 Trash cans"
        if label == "skip":
            embed.description = f"🤷 {who} couldn't tell; not used for learning."
            embed.colour = _COLOUR_DONE
            reply = "Skipped — blurry or blocked shots are better left out."
            await self.hass.async_add_executor_job(self._move_sample, sid, None)
        else:
            cans["refs"] = trash.add_reference(
                cans["refs"],
                {
                    "label": label,
                    "roi": sample["roi"],
                    "features": sample["features"],
                    "at": time_mod.time(),
                    "by": who,
                },
            )
            await self._async_refresh_model()
            word = "out" if label == vision.LABEL_OUT else "not out"
            embed.description = f"{'🗑️' if label == 'out' else '🏠'} {who} says: **{word}**."
            embed.colour = _COLOUR_OK
            guess = sample.get("guess")
            verdict_line = ""
            if guess:
                right = guess == label
                verdict_line = f" My guess was {'right ✓' if right else 'wrong ✗'}."
            embed.set_footer(text=f"Learning: {self.model.summary()}")
            needed = ""
            if not self.model.ready:
                needed = (
                    f" I start trusting myself at {self.min_labels} of each and "
                    f"{round(self.min_accuracy * 100)}% on the self-test."
                )
            reply = f"Thanks, learned.{verdict_line} {self.model.summary()}.{needed}"
            await self.hass.async_add_executor_job(self._move_sample, sid, label)
            if label == vision.LABEL_OUT and sample.get("night"):
                await self._async_resolve_night(sample["night"], "out", who, uid)
        self._save()
        self._signal()
        return embed, reply

    # --- trash cans: the night reminder -----------------------------------------

    def _night(self, key: str) -> dict:
        night = self.state["cans"].get("night")
        if not isinstance(night, dict) or night.get("key") != key:
            night = {"key": key, "message_id": None, "resolved": None, "by": None,
                     "renagged": False, "chore_marked": False}
            self.state["cans"]["night"] = night
        return night

    async def _tick_night(self, _now) -> None:
        try:
            await self.async_cans_night()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Cans-night reminder failed")

    def _mention(self) -> str:
        parts = []
        if self.nag_mention:
            parts.append(self.nag_mention)
        chore = self.find_chore(self.cans_chore) if self.cans_chore != NO_CHORE else None
        if chore:
            item = chores_mod.entry(self.state["chores"], chore.key)
            if chores_mod.claimant(item, time_mod.time()) and item.get("claimed_by_id"):
                parts.append(f"<@{item['claimed_by_id']}>")
        return " ".join(parts)

    async def async_cans_night(self, *, force: bool = False) -> None:
        local = dt_util.now()
        if not force and not trash.is_cans_night(local, self.nights):
            return
        key = trash.night_key(local)
        night = self._night(key)
        if night["resolved"]:
            return
        # Take one fresh look first, without asking (the reminder asks instead).
        await self.async_check_cans(manual=False, allow_ask=False)
        if night["resolved"]:
            return  # the fresh look saw them out
        last = self.state["cans"].get("last") or {}
        plan = trash.plan_nag(
            ready=self.model.ready, last_result=last.get("result"), last_at=last.get("at"),
            now=time_mod.time(),
        )
        if plan.kind == "none":
            await self._async_resolve_night(key, "out", "📷 the camera", None)
            return
        await self._async_post_nag(key, plan, first=True)
        renag = int(self._opts.get(CONF_RENAG, DEFAULT_RENAG))
        if renag > 0:
            self._unsubs.append(
                async_call_later(self.hass, renag * 60, self._renag_later(key))
            )

    def _renag_later(self, key: str):
        async def _run(_now) -> None:
            try:
                night = self._night(key)
                if night["resolved"] or night["renagged"]:
                    return
                night["renagged"] = True
                await self.async_check_cans(manual=False, allow_ask=False)
                if night["resolved"]:
                    return
                last = self.state["cans"].get("last") or {}
                plan = trash.plan_nag(
                    ready=self.model.ready, last_result=last.get("result"),
                    last_at=last.get("at"), now=time_mod.time(),
                )
                if plan.kind != "none":
                    await self._async_post_nag(key, plan, first=False)
                else:
                    await self._async_resolve_night(key, "out", "📷 the camera", None)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Second cans reminder failed")

        return _run

    async def _async_post_nag(self, key: str, plan: trash.NagPlan, *, first: bool) -> None:
        night = self._night(key)
        recent = self._last_crop if (
            self._last_crop and time_mod.time() - self._last_crop[0] < _CROP_REUSE_S
        ) else None
        last = self.state["cans"].get("last") or {}
        usable_image = recent is not None and last.get("result") != trash.DARK

        if plan.kind == "nag":
            title = "🗑️ Trash night — the cans aren't out yet"
            colour = _COLOUR_WARN
            body = "The camera doesn't see them at the curb."
        else:
            title = "🗑️ Trash night — are the cans out?"
            colour = _COLOUR_ASK
            body = (
                "I'm still learning what the curb looks like, so tell me."
                if not self.model.ready
                else "I can't tell from here, so tell me."
            )
        if not first:
            title = "🔁 " + title
        embed = discord.Embed(title=title, description=body, colour=colour,
                              timestamp=dt_util.utcnow())
        files = None
        sid = None
        if usable_image:
            embed.set_image(url="attachment://cans.jpg")
            files = [("cans.jpg", recent[1])]
            if plan.kind == "ask":
                # Answering also labels the picture.
                sid = trash.new_sample_id()
        embed.set_footer(text=f"Learning: {self.model.summary()}")
        mention = self._mention()
        try:
            message_id = await self._bot.async_post(
                self.cans_channel_id,
                content=mention or None,
                embed=embed,
                view=nag_view(key, with_labels_sid=sid),
                files=files,
                silent=False,
                mention=bool(mention),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not post the cans reminder", exc_info=True)
            return
        night["message_id"] = message_id
        if sid is not None:
            # Rebuild the analysis from the recent check's stored features.
            pending_features = self._recent_features()
            if pending_features is not None:
                self.state["cans"]["pending"][sid] = {
                    "at": time_mod.time(), "roi": self.roi_key, "features": pending_features,
                    "guess": last.get("guess"), "confidence": last.get("confidence"),
                    "night": key, "message_id": message_id,
                }
        self._save()

    def _recent_features(self) -> dict | None:
        return (self.state["cans"].get("last") or {}).get("features")

    async def async_nag_action(
        self, key: str, action: str, who: str, uid: int | None, base: discord.Embed | None
    ) -> discord.Embed | None:
        night = self.state["cans"].get("night")
        if not isinstance(night, dict) or night.get("key") != key:
            return None
        await self._async_resolve_night(key, action, who, uid, edit=False)
        embed = base.copy() if base is not None else discord.Embed()
        embed.colour = _COLOUR_OK if action == "out" else _COLOUR_DONE
        embed.description = (
            f"✅ {who} says the cans are out. Thanks!"
            if action == "out"
            else f"⏭️ {who} says there's no pickup this week."
        )
        return embed

    async def _async_resolve_night(
        self, key: str, how: str, who: str, uid: int | None, *, edit: bool = True
    ) -> None:
        night = self._night(key)
        if night["resolved"]:
            return
        night["resolved"] = how
        night["by"] = who
        if how == "out" and not night["chore_marked"] and self.cans_chore != NO_CHORE:
            night["chore_marked"] = True
            await self.async_mark_done(self.cans_chore, who, uid, refresh=False)
        if edit and night.get("message_id"):
            try:
                message = await self._bot.async_fetch(self.cans_channel_id, night["message_id"])
                embed = message.embeds[0].copy() if message.embeds else discord.Embed()
                embed.colour = _COLOUR_OK
                embed.description = f"✅ Cans are out (seen by {who})."
                await message.edit(embed=embed, view=None)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not update the night reminder", exc_info=True)
        self._save()
        self._signal()
        await self.async_update_board()

    # --- trash cans: setup helpers ----------------------------------------------

    async def async_preview_region(self) -> str:
        if not self.camera:
            return "No camera configured."
        try:
            content = await _snapshot(self.hass, self.camera)
        except HomeAssistantError as err:
            return f"Couldn't get a snapshot: {err}"
        preview = await self.hass.async_add_executor_job(vision.roi_preview, content, self.roi)
        analysis = await self.hass.async_add_executor_job(vision.analyse, content, self.roi)
        embed = discord.Embed(
            title="🟥 Where I look for the cans",
            description=(
                "The red box is the only part of the picture I compare. Make it "
                "snug around where the cans stand at the curb, with a little "
                "margin. Adjust it under **Configure** (percent of width and "
                "height from the top-left). Changing it starts the learning over."
            ),
            colour=_COLOUR_ASK,
        )
        embed.set_image(url="attachment://region.jpg")
        embed.set_thumbnail(url="attachment://crop.jpg")
        await self._bot.async_post(
            self.cans_channel_id,
            embed=embed,
            files=[("region.jpg", preview), ("crop.jpg", analysis.crop_jpeg)],
        )
        return "Posted."

    async def async_reset_cans(self) -> None:
        self.state["cans"] = _blank_state()["cans"]
        await self._async_refresh_model()
        self._save()
        self._signal()
        await self.async_update_board()

    # Sample files: /config/chores_discord/{pending,out,in}/<sid>.jpg

    def _samples_dir(self, label: str) -> str:
        return self.hass.config.path(SAMPLES_DIR, label)

    def _write_sample(self, label: str, sid: str, data: bytes) -> None:
        folder = self._samples_dir(label)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{sid}.jpg"), "wb") as handle:
            handle.write(data)
        self._trim(folder)

    def _move_sample(self, sid: str, label: str | None) -> None:
        source = os.path.join(self._samples_dir("pending"), f"{sid}.jpg")
        if not os.path.exists(source):
            return
        if label is None:
            os.remove(source)
            return
        folder = self._samples_dir(label)
        os.makedirs(folder, exist_ok=True)
        os.replace(source, os.path.join(folder, f"{sid}.jpg"))
        self._trim(folder)

    @staticmethod
    def _trim(folder: str) -> None:
        files = sorted(
            (os.path.join(folder, name) for name in os.listdir(folder)),
            key=os.path.getmtime,
        )
        for path in files[:-SAMPLES_KEEP]:
            try:
                os.remove(path)
            except OSError:
                pass

    # --- for entities -------------------------------------------------------------

    def cans_attributes(self) -> dict:
        last = self.state["cans"].get("last") or {}
        return {
            "checked_at": dt_util.utc_from_timestamp(last["at"]).isoformat()
            if last.get("at") else None,
            "guess": last.get("guess"),
            "confidence": last.get("confidence"),
            "brightness": last.get("brightness"),
            "labels_out": self.model.out,
            "labels_not_out": self.model.not_out,
            "self_test_accuracy": self.model.accuracy,
            "phase": self.model.phase,
            "nagging_trusted": self.model.ready,
        }
