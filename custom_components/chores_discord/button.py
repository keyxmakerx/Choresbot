"""Buttons: one-tap versions of the actions, for a dashboard."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ChoresConfigEntry
from .entity import ChoresEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChoresConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    buttons = [PostBoardButton(coordinator, entry, "post_board")]
    if coordinator.cans_enabled:
        buttons += [
            CheckCansButton(coordinator, entry, "check_cans"),
            PreviewRegionButton(coordinator, entry, "preview_region"),
        ]
    async_add_entities(buttons)


class PostBoardButton(ChoresEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.coordinator.async_update_board(repost=True)


class CheckCansButton(ChoresEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.coordinator.async_check_cans(manual=True)


class PreviewRegionButton(ChoresEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.coordinator.async_preview_region()
