"""discord.py layer for the Chores Discord Bot: cards, controls and posting.

Messages use Discord's Components V2 layout: one card (a container with an
accent colour) holding headings, dividers, a full-width photo and the
controls, instead of a classic embed with buttons hanging underneath.

Like the laundry bot, the client runs *inside* Home Assistant's event loop as
a background task tied to the config entry, needs no privileged intents and
no open ports. Every control is a ``DynamicItem`` whose custom id says what it
acts on, so taps on any message route correctly after a restart, and every
callback catches its own errors so Discord can't bubble into Home Assistant.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import io
import logging
import re
from typing import TYPE_CHECKING, Any

import discord
from discord import ui

from .const import (
    BOARD_BUTTON_TEMPLATE,
    BOARD_SELECT_TEMPLATE,
    CANS_LABEL_TEMPLATE,
    CANS_NAG_TEMPLATE,
    QUICK_TEMPLATE,
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


async def _private(interaction: discord.Interaction, text: str | None, view=None) -> None:
    """A private follow-up after the card's own edit; never raises."""
    if not text:
        return
    try:
        kwargs = {"view": view} if view is not None else {}
        await interaction.followup.send(text, ephemeral=True, **kwargs)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not send a private follow-up", exc_info=True)


def image_url(interaction: discord.Interaction, filename: str) -> str:
    """Where the tapped message's photo lives, to keep it when re-rendering."""
    message = getattr(interaction, "message", None)
    for attachment in getattr(message, "attachments", None) or []:
        if attachment.filename == filename:
            return attachment.url
    return f"attachment://{filename}"


# --- card data (plain values the coordinator fills in) ----------------------------


@dataclass
class BoardCard:
    colour: int
    title: str
    sections: list[tuple[str, list[str]]]
    quick: list[tuple[str, str, str]]  # key, emoji, name
    chores: list[tuple[str, str, str, str]]  # key, emoji, name, note
    bins_text: str | None
    cans_enabled: bool
    footer: str


@dataclass
class BinRow:
    bin: str
    emoji: str
    name: str
    text: str  # the guess, or the answer once given
    answered: bool


@dataclass
class AskCard:
    sid: str
    colour: int
    title: str
    image: str
    rows: list[BinRow]
    footer: str
    closed: bool = False
    note: str | None = None


@dataclass
class NagCard:
    night: str
    colour: int
    mention: str
    title: str
    lines: list[str]
    image: str | None
    open_bins: list[tuple[str, str, str]] = field(default_factory=list)  # bin, emoji, name
    footer: str = ""
    sid: str | None = None  # a usable snapshot: "is out" also labels it


# --- builders ------------------------------------------------------------------


def board_view(card: BoardCard) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    items: list[Any] = [ui.TextDisplay(f"## {card.title}")]
    for index, (name, rows) in enumerate(card.sections):
        if index:
            items.append(ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small))
        items.append(ui.TextDisplay(f"**{name}**\n" + "\n".join(rows)))
    if not card.sections:
        items.append(ui.TextDisplay("No chores configured."))
    items.append(ui.Separator())
    if card.quick:
        items.append(ui.TextDisplay("-# Quick report: tap what needs doing"))
        row = ui.ActionRow()
        for key, emoji, name in card.quick:
            row.add_item(QuickButton(key, emoji, name))
        items.append(row)
    if card.chores:
        items.append(ui.ActionRow(BoardSelect("done", card.chores)))
        items.append(ui.ActionRow(BoardSelect("claim", card.chores)))
    if card.bins_text:
        items.append(ui.Separator())
        items.append(ui.TextDisplay(card.bins_text))
    buttons = ui.ActionRow(BoardButton("refresh"))
    if card.cans_enabled:
        buttons.add_item(BoardButton("check"))
    items.append(buttons)
    items.append(ui.TextDisplay(f"-# {card.footer}"))
    view.add_item(ui.Container(*items, accent_colour=card.colour))
    return view


def ask_view(card: AskCard) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    items: list[Any] = [
        ui.TextDisplay(f"### {card.title}"),
        ui.MediaGallery(discord.MediaGalleryItem(card.image)),
        ui.TextDisplay("\n".join(f"{r.emoji} **{r.name}** · {r.text}" for r in card.rows)),
    ]
    if card.note:
        items.append(ui.TextDisplay(card.note))
    if not card.closed:
        for r in card.rows:
            if not r.answered:
                items.append(
                    ui.ActionRow(
                        LabelButton(r.bin, "out", card.sid, f"{r.name} out", r.emoji),
                        LabelButton(r.bin, "in", card.sid, "Not out", None),
                    )
                )
        items.append(ui.ActionRow(LabelButton("all", "skip", card.sid, "Can't tell", "🤷")))
    items.append(ui.TextDisplay(f"-# {card.footer}"))
    view.add_item(ui.Container(*items, accent_colour=card.colour))
    return view


def nag_view(card: NagCard) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    head = f"{card.mention}\n" if card.mention else ""
    items: list[Any] = [ui.TextDisplay(f"{head}### {card.title}\n" + "\n".join(card.lines))]
    if card.image:
        items.append(ui.MediaGallery(discord.MediaGalleryItem(card.image)))
    if card.open_bins:
        row = ui.ActionRow()
        for bin_name, emoji, name in card.open_bins:
            row.add_item(NagButton(bin_name, "out", card.night, f"{name} is out", emoji))
        row.add_item(NagButton("all", "skip", card.night, "No pickup this week", "⏭️"))
        items.append(row)
    if card.footer:
        items.append(ui.TextDisplay(f"-# {card.footer}"))
    view.add_item(ui.Container(*items, accent_colour=card.colour))
    return view


def preview_view(text: str, colour: int) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            ui.TextDisplay(text),
            ui.MediaGallery(
                discord.MediaGalleryItem("attachment://region.jpg", description="Whole snapshot"),
                discord.MediaGalleryItem("attachment://crop.jpg", description="What it compares"),
            ),
            accent_colour=colour,
        )
    )
    return view


# --- controls ------------------------------------------------------------------


class BoardSelect(ui.DynamicItem[ui.Select], template=BOARD_SELECT_TEMPLATE):
    """"I did a chore…" / "I'll take a chore…"."""

    def __init__(self, what: str, chores: list[tuple[str, str, str, str]] | None = None) -> None:
        options = [
            discord.SelectOption(
                label=name[:100], value=key, emoji=emoji or None,
                description=(note[:100] if note else None),
            )
            for key, emoji, name, note in (chores or [])
        ] or [discord.SelectOption(label="…", value="_")]
        super().__init__(
            ui.Select(
                custom_id=f"chores:board:{what}",
                placeholder="✅ I did a chore…" if what == "done" else "🙋 I'll take a chore…",
                options=options[:25],
            )
        )
        self.what = what

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        return cls(match["what"])

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        key = self.item.values[0]
        who, uid = interaction.user.display_name, interaction.user.id
        try:
            if self.what == "done":
                chore = await coordinator.async_mark_done(key, who, uid, refresh=False)
                if chore is None:
                    await interaction.response.send_message(
                        "That chore isn't on the list any more.", ephemeral=True
                    )
                    return
                await interaction.response.edit_message(view=coordinator.board_layout())
                await _private(
                    interaction, f"Nice — {chore.emoji} **{chore.name}** marked done.", UndoView(key)
                )
            else:
                result = await coordinator.async_toggle_claim(key, who, uid)
                if result is None:
                    await interaction.response.send_message(
                        "That chore isn't on the list any more.", ephemeral=True
                    )
                    return
                chore, claimed = result
                await interaction.response.edit_message(view=coordinator.board_layout())
                await _private(
                    interaction,
                    f"{chore.emoji} **{chore.name}** is yours."
                    if claimed
                    else f"Dropped your claim on {chore.emoji} **{chore.name}**.",
                )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Board select failed")
            await _safe_error(interaction)


class BoardButton(ui.DynamicItem[ui.Button], template=BOARD_BUTTON_TEMPLATE):
    def __init__(self, what: str) -> None:
        label, emoji = ("Refresh", "🔄") if what == "refresh" else ("Check the bins", "📸")
        super().__init__(
            ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                      custom_id=f"chores:board:{what}")
        )
        self.what = what

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        return cls(match["what"])

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            if self.what == "refresh":
                await interaction.response.edit_message(view=coordinator.board_layout())
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            text = await coordinator.async_check_cans(manual=True)
            await interaction.followup.send(text, ephemeral=True)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Board button failed")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Couldn't do that just now.", ephemeral=True)
                else:
                    await _safe_error(interaction)
            except Exception:  # noqa: BLE001
                pass


class QuickButton(ui.DynamicItem[ui.Button], template=QUICK_TEMPLATE):
    """One tap: "this needs doing now"."""

    def __init__(self, key: str, emoji: str | None = None, name: str | None = None) -> None:
        super().__init__(
            ui.Button(label=(name or key)[:80], emoji=emoji or None,
                      style=discord.ButtonStyle.primary, custom_id=f"chores:quick:{key}")
        )
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        return cls(match["key"])

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            result = await coordinator.async_report(self.key, interaction.user.display_name)
            if result is None:
                await interaction.response.send_message(
                    "That chore isn't on the list any more.", ephemeral=True
                )
                return
            chore, fresh = result
            await interaction.response.edit_message(view=coordinator.board_layout())
            await _private(
                interaction,
                f"🔔 Flagged {chore.emoji} **{chore.name}** as needing doing."
                if fresh
                else f"{chore.emoji} **{chore.name}** was already flagged.",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Quick report failed")
            await _safe_error(interaction)


class _UndoButton(ui.Button):
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


class UndoView(ui.View):
    """Private, short-lived: offered right after you mark something done."""

    def __init__(self, key: str) -> None:
        super().__init__(timeout=600)
        self.add_item(_UndoButton(key))


class LabelButton(ui.DynamicItem[ui.Button], template=CANS_LABEL_TEMPLATE):
    """An answer on a snapshot: this bin is out / not out, or can't tell."""

    def __init__(self, bin_name: str, label: str, sid: str,
                 text: str | None = None, emoji: str | None = None) -> None:
        style = discord.ButtonStyle.success if label == "out" else discord.ButtonStyle.secondary
        super().__init__(
            ui.Button(label=text or label, emoji=emoji, style=style,
                      custom_id=f"chores:cans:{bin_name}:{label}:{sid}")
        )
        self.bin, self.label, self.sid = bin_name, label, sid

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        return cls(match["bin"], match["label"], match["sid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            reply = await coordinator.async_label(
                self.sid, self.bin, self.label, interaction.user.display_name, interaction.user.id,
                edit_message=False,
            )
            if reply is None:
                await interaction.response.send_message(
                    "That snapshot has already been answered or has expired.", ephemeral=True
                )
                return
            layout = coordinator.ask_layout(self.sid, image_url(interaction, "cans.jpg"))
            if layout is not None:
                await interaction.response.edit_message(view=layout)
            else:
                await interaction.response.defer()
            await _private(interaction, reply)
            await coordinator.async_update_board()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Bin label failed")
            await _safe_error(interaction)


class NagButton(ui.DynamicItem[ui.Button], template=CANS_NAG_TEMPLATE):
    """On a bin-night reminder: say a bin is out, or skip this week."""

    def __init__(self, bin_name: str, action: str, night: str,
                 text: str | None = None, emoji: str | None = None) -> None:
        style = discord.ButtonStyle.success if action == "out" else discord.ButtonStyle.secondary
        super().__init__(
            ui.Button(label=text or action, emoji=emoji, style=style,
                      custom_id=f"chores:nag:{bin_name}:{action}:{night}")
        )
        self.bin, self.action, self.night = bin_name, action, night

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        return cls(match["bin"], match["action"], match["night"])

    async def callback(self, interaction: discord.Interaction) -> None:
        coordinator = _coordinator(interaction)
        try:
            ok = await coordinator.async_nag_action(
                self.night, self.bin, self.action, interaction.user.display_name, interaction.user.id,
            )
            if not ok:
                await interaction.response.send_message(
                    "That reminder is from another night.", ephemeral=True
                )
                return
            layout = coordinator.nag_layout(self.night, image_url(interaction, "cans.jpg"))
            if layout is not None:
                await interaction.response.edit_message(view=layout)
            else:
                await interaction.response.defer()
            await coordinator.async_update_board()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Nag action failed")
            await _safe_error(interaction)


# --- client and helper -----------------------------------------------------------


class ChoresDiscordClient(discord.Client):
    def __init__(self, coordinator: "ChoresCoordinator", **kwargs) -> None:
        super().__init__(**kwargs)
        self.coordinator = coordinator

    async def setup_hook(self) -> None:
        # Registered once, before connecting, so taps on old messages route.
        self.add_dynamic_items(BoardSelect, BoardButton, QuickButton, LabelButton, NagButton)

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
        view: ui.LayoutView,
        *,
        files: list[tuple[str, bytes]] | None = None,
        silent: bool = True,
        mention: bool = False,
    ) -> int:
        """Post a card; returns its id. ``mention`` lets user/role mentions ping."""
        await self._wait_ready()
        channel = await self._channel(channel_id)
        attachments = [discord.File(io.BytesIO(data), filename=name) for name, data in files or []]
        message = await channel.send(
            view=view,
            files=attachments or discord.utils.MISSING,
            silent=silent,
            allowed_mentions=(
                discord.AllowedMentions(users=True, roles=True, everyone=False)
                if mention
                else discord.AllowedMentions.none()
            ),
        )
        return message.id

    async def async_fetch(self, channel_id: int, message_id: int) -> discord.Message:
        await self._wait_ready()
        channel = await self._channel(channel_id)
        return await channel.fetch_message(message_id)

    async def async_edit(self, channel_id: int, message_id: int, view: ui.LayoutView) -> None:
        message = await self.async_fetch(channel_id, message_id)
        await message.edit(view=view)

    async def async_edit_keeping(
        self, channel_id: int, message_id: int, build, filename: str = "cans.jpg"
    ) -> None:
        """Re-render a card that carries a photo, pointing at the stored photo."""
        message = await self.async_fetch(channel_id, message_id)
        url = next(
            (a.url for a in message.attachments if a.filename == filename),
            f"attachment://{filename}",
        )
        view = build(url)
        if view is not None:
            await message.edit(view=view)

    async def async_delete(self, channel_id: int, message_id: int) -> None:
        try:
            message = await self.async_fetch(channel_id, message_id)
            await message.delete()
        except discord.NotFound:
            pass
