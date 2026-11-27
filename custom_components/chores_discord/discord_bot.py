"""discord.py layer for the Chores Discord Bot: the board, buttons and posts.

Like the laundry bot, the client runs *inside* Home Assistant's event loop as
a background task tied to the config entry (never ``client.run()``), needs no
privileged intents and no open ports. Every callback catches its own errors so
a Discord problem can never bubble into Home Assistant.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from typing import TYPE_CHECKING

import discord

from .const import (
    BOARD_CANS_ID,
    BOARD_CLAIM_ID,
    BOARD_DONE_ID,
    BOARD_REFRESH_ID,
    CANS_LABEL_TEMPLATE,
    CANS_NAG_TEMPLATE,
)

if TYPE_CHECKING:
    from .coordinator import ChoresCoordinator

_LOGGER = logging.getLogger(__name__)

# How long a send waits for the gateway before giving up, so a dead login
# can't hang a caller forever (same reasoning as the laundry bot).
_READY_TIMEOUT = 30


def _coordinator(interaction: discord.Interaction) -> "ChoresCoordinator":
    return interaction.client.coordinator  # type: ignore[attr-defined]


async def _safe_error(interaction: discord.Interaction) -> None:
    """Best-effort private error reply; never raises."""
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Something went wrong — try again in a moment.", ephemeral=True
            )
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not send interaction error", exc_info=True)


def _base_embed(interaction: discord.Interaction) -> discord.Embed | None:
    """The embed on the tapped message, to edit rather than replace."""
    message = getattr(interaction, "message", None)
    if message is not None and message.embeds:
        return message.embeds[0]
    return None


async def _refresh_board_quietly(coordinator: "ChoresCoordinator") -> None:
    try:
        await coordinator.async_update_board()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Board refresh after a tap failed", exc_info=True)


# --- the chore board ------------------------------------------------------------


class _ChoreSelect(discord.ui.Select):
    """One select listing every chore; ``mode`` is "done" or "claim"."""

    def __init__(self, coordinator: "ChoresCoordinator | None", mode: str) -> None:
        options = []
        if coordinator is not None:
            for chore in coordinator.chores:
                options.append(
                    discord.SelectOption(
                        label=chore.name[:100],
                        value=chore.key,
                        emoji=chore.emoji or None,
                        description=(chore.note or None) and chore.note[:100],
                    )
                )
        if not options:
            # A template view (registered for routing only) still needs one.
            options = [discord.SelectOption(label="…", value="_")]
        super().__init__(
            custom_id=BOARD_DONE_ID if mode == "done" else BOARD_CLAIM_ID,
            placeholder="✅ I did a chore…" if mode == "done" else "🙋 I'll take a chore…",
            min_values=1,
            max_values=1,
            options=options,
            row=0 if mode == "done" else 1,
        )
        self.mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        key = self.values[0]
        who = interaction.user.display_name
        uid = interaction.user.id
        try:
            if self.mode == "done":
                chore = await coordinator.async_mark_done(key, who, uid, refresh=False)
                if chore is None:
                    await interaction.response.send_message(
                        "That chore isn't on the list any more.", ephemeral=True
                    )
                    return
                await interaction.response.edit_message(
                    embed=coordinator.board_embed(), view=BoardView(coordinator)
                )
                await interaction.followup.send(
                    f"Nice — {chore.emoji} **{chore.name}** marked done.",
                    view=UndoView(key),
                    ephemeral=True,
                )
            else:
                result = await coordinator.async_toggle_claim(key, who, uid)
                if result is None:
                    await interaction.response.send_message(
                        "That chore isn't on the list any more.", ephemeral=True
                    )
                    return
                chore, claimed = result
                await interaction.response.edit_message(
                    embed=coordinator.board_embed(), view=BoardView(coordinator)
                )
                await interaction.followup.send(
                    f"{chore.emoji} **{chore.name}** is yours."
                    if claimed
                    else f"Dropped your claim on {chore.emoji} **{chore.name}**.",
                    ephemeral=True,
                )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Board select failed")
            await _safe_error(interaction)


class _RefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary,
            custom_id=BOARD_REFRESH_ID, row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            await interaction.response.edit_message(
                embed=coordinator.board_embed(), view=BoardView(coordinator)
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Board refresh failed")
            await _safe_error(interaction)


class _CansButton(discord.ui.Button):
    """Take a snapshot now and ask about it (also how you teach it on demand)."""

    def __init__(self) -> None:
        super().__init__(
            label="Check the cans", emoji="📸", style=discord.ButtonStyle.secondary,
            custom_id=BOARD_CANS_ID, row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            text = await coordinator.async_check_cans(manual=True)
            await interaction.followup.send(text, ephemeral=True)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Manual can check failed")
            try:
                await interaction.followup.send(
                    "Couldn't check the cans just now.", ephemeral=True
                )
            except Exception:  # noqa: BLE001
                pass


class BoardView(discord.ui.View):
    """The board's controls. ``coordinator=None`` builds the routing template."""

    def __init__(self, coordinator: "ChoresCoordinator | None" = None) -> None:
        super().__init__(timeout=None)
        self.add_item(_ChoreSelect(coordinator, "done"))
        self.add_item(_ChoreSelect(coordinator, "claim"))
        self.add_item(_RefreshButton())
        if coordinator is None or coordinator.cans_enabled:
            self.add_item(_CansButton())


class _UndoButton(discord.ui.Button):
    def __init__(self, key: str) -> None:
        super().__init__(label="Undo", emoji="↩️", style=discord.ButtonStyle.secondary)
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            ok = await coordinator.async_undo(self.key, interaction.user.id)
            await interaction.response.edit_message(
                content="Undone." if ok else "Nothing to undo (only the person who marked it can).",
                view=None,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Undo failed")
            await _safe_error(interaction)


class UndoView(discord.ui.View):
    """Private, short-lived: offered right after you mark something done."""

    def __init__(self, key: str) -> None:
        super().__init__(timeout=600)
        self.add_item(_UndoButton(key))


# --- trash cans ----------------------------------------------------------------

_LABEL_TEXT = {
    "out": ("Cans out", "🗑️", discord.ButtonStyle.success),
    "in": ("Not out", "🏠", discord.ButtonStyle.secondary),
    "skip": ("Can't tell", "🤷", discord.ButtonStyle.secondary),
}


class CansLabelButton(
    discord.ui.DynamicItem[discord.ui.Button], template=CANS_LABEL_TEMPLATE
):
    """A label button on a snapshot. The sample id rides in the custom id, so
    the buttons keep working across restarts with nothing kept in memory."""

    def __init__(self, label: str, sid: str) -> None:
        text, emoji, style = _LABEL_TEXT[label]
        super().__init__(
            discord.ui.Button(
                label=text, emoji=emoji, style=style,
                custom_id=f"chores:cans:{label}:{sid}",
            )
        )
        self.label = label
        self.sid = sid

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> "CansLabelButton":
        return cls(match["label"], match["sid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            result = await coordinator.async_label(
                self.sid, self.label, interaction.user.display_name, interaction.user.id,
                _base_embed(interaction),
            )
            if result is None:
                await interaction.response.send_message(
                    "That snapshot has already been answered or has expired.",
                    ephemeral=True,
                )
                return
            embed, reply = result
            await interaction.response.edit_message(embed=embed, view=None)
            if reply:
                await interaction.followup.send(reply, ephemeral=True)
            await _refresh_board_quietly(coordinator)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Can label failed")
            await _safe_error(interaction)


def label_view(sid: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for label in ("out", "in", "skip"):
        view.add_item(CansLabelButton(label, sid))
    return view


_NAG_TEXT = {
    "out": ("They're out", "✅", discord.ButtonStyle.success),
    "skip": ("No pickup this week", "⏭️", discord.ButtonStyle.secondary),
}


class CansNagButton(
    discord.ui.DynamicItem[discord.ui.Button], template=CANS_NAG_TEMPLATE
):
    """On a night reminder: say they're out (silences it) or skip this week."""

    def __init__(self, action: str, night: str) -> None:
        text, emoji, style = _NAG_TEXT[action]
        super().__init__(
            discord.ui.Button(
                label=text, emoji=emoji, style=style,
                custom_id=f"chores:nag:{action}:{night}",
            )
        )
        self.action = action
        self.night = night

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> "CansNagButton":
        return cls(match["action"], match["night"])

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            embed = await coordinator.async_nag_action(
                self.night, self.action, interaction.user.display_name, interaction.user.id,
                _base_embed(interaction),
            )
            if embed is None:
                await interaction.response.send_message(
                    "That reminder is from another night.", ephemeral=True
                )
                return
            await interaction.response.edit_message(embed=embed, view=None)
            await _refresh_board_quietly(coordinator)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Nag action failed")
            await _safe_error(interaction)


def nag_view(night: str, *, with_labels_sid: str | None = None) -> discord.ui.View:
    """Reminder buttons; with a usable snapshot, the label buttons too."""
    view = discord.ui.View(timeout=None)
    if with_labels_sid:
        for label in ("out", "in"):
            view.add_item(CansLabelButton(label, with_labels_sid))
    else:
        view.add_item(CansNagButton("out", night))
    view.add_item(CansNagButton("skip", night))
    return view


# --- client and helper -----------------------------------------------------------


class ChoresDiscordClient(discord.Client):
    def __init__(self, coordinator: "ChoresCoordinator", **kwargs) -> None:
        super().__init__(**kwargs)
        self.coordinator = coordinator
        self._registered = False

    async def setup_hook(self) -> None:
        # Registered once, before connecting, so taps on old messages route.
        self.add_view(BoardView(None))
        self.add_dynamic_items(CansLabelButton, CansNagButton)
        self._registered = True

    async def on_ready(self) -> None:
        _LOGGER.debug("Chores bot connected as %s", self.user)
        try:
            await self.coordinator.async_on_bot_ready()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error during post-ready restore")


class DiscordBot:
    """What the coordinator uses to post, edit and delete messages."""

    def __init__(self, coordinator: "ChoresCoordinator", token: str) -> None:
        self._token = token
        self._client = ChoresDiscordClient(coordinator, intents=discord.Intents.default())

    async def async_start(self) -> None:
        await self._client.start(self._token)

    async def async_close(self) -> None:
        if not self._client.is_closed():
            await self._client.close()

    @property
    def is_ready(self) -> bool:
        return self._client.is_ready()

    async def _wait_ready(self) -> None:
        async with asyncio.timeout(_READY_TIMEOUT):
            await self._client.wait_until_ready()

    async def _channel(self, channel_id: int):
        channel = self._client.get_channel(channel_id)
        if channel is None:
            channel = await self._client.fetch_channel(channel_id)
        return channel

    async def async_post(
        self,
        channel_id: int,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        content: str | None = None,
        files: list[tuple[str, bytes]] | None = None,
        silent: bool = True,
        mention: bool = False,
    ) -> int:
        """Post a message; returns its id. ``mention`` allows user/role pings."""
        await self._wait_ready()
        channel = await self._channel(channel_id)
        attachments = [
            discord.File(io.BytesIO(data), filename=name) for name, data in files or []
        ]
        kwargs = {}
        if view is not None:
            kwargs["view"] = view
        message = await channel.send(
            content=content,
            embed=embed,
            files=attachments or discord.utils.MISSING,
            silent=silent,
            allowed_mentions=(
                discord.AllowedMentions(users=True, roles=True, everyone=False)
                if mention
                else discord.AllowedMentions.none()
            ),
            **kwargs,
        )
        return message.id

    async def async_fetch(self, channel_id: int, message_id: int) -> discord.Message:
        await self._wait_ready()
        channel = await self._channel(channel_id)
        return await channel.fetch_message(message_id)

    async def async_edit(
        self,
        channel_id: int,
        message_id: int,
        *,
        embed: discord.Embed,
        view: discord.ui.View | None = discord.utils.MISSING,
    ) -> None:
        message = await self.async_fetch(channel_id, message_id)
        await message.edit(embed=embed, view=view)

    async def async_delete(self, channel_id: int, message_id: int) -> None:
        try:
            message = await self.async_fetch(channel_id, message_id)
            await message.delete()
        except discord.NotFound:
            pass
