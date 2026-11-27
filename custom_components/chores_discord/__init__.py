"""The Chores Discord Bot integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    PLATFORMS,
    SERVICE_CHECK_CANS,
    SERVICE_MARK_DONE,
    SERVICE_POST_BOARD,
    SERVICE_PREVIEW_REGION,
    SERVICE_RESET_CANS,
)
from .coordinator import ChoresConfigEntry, ChoresCoordinator
from .panel import async_remove_panel, async_setup_panel

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_MARK_DONE_SCHEMA = vol.Schema(
    {vol.Required("chore"): cv.string, vol.Optional("who", default="Home Assistant"): cv.string}
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ChoresConfigEntry) -> bool:
    coordinator = ChoresCoordinator(hass, entry)
    await coordinator.async_setup()
    entry.runtime_data = coordinator
    entry.async_create_background_task(hass, coordinator.async_run_bot(), f"{DOMAIN}_bot")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await async_setup_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ChoresConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_shutdown()
    others = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
    ]
    if not others:
        async_remove_panel(hass)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ChoresConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _loaded(hass: HomeAssistant) -> list[ChoresCoordinator]:
    coordinators = [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not coordinators:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return coordinators


def _async_register_services(hass: HomeAssistant) -> None:
    async def _post_board(call: ServiceCall) -> None:
        for coordinator in _loaded(hass):
            await coordinator.async_update_board(repost=True)

    async def _mark_done(call: ServiceCall) -> None:
        for coordinator in _loaded(hass):
            if await coordinator.async_mark_done(call.data["chore"], call.data["who"], None) is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_chore",
                    translation_placeholders={"chore": call.data["chore"]},
                )

    async def _check_cans(call: ServiceCall) -> None:
        for coordinator in _loaded(hass):
            await coordinator.async_check_cans(manual=True)

    async def _preview(call: ServiceCall) -> None:
        for coordinator in _loaded(hass):
            await coordinator.async_preview_region()

    async def _reset(call: ServiceCall) -> None:
        for coordinator in _loaded(hass):
            await coordinator.async_reset_cans()

    hass.services.async_register(DOMAIN, SERVICE_POST_BOARD, _post_board)
    hass.services.async_register(DOMAIN, SERVICE_MARK_DONE, _mark_done, schema=_MARK_DONE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHECK_CANS, _check_cans)
    hass.services.async_register(DOMAIN, SERVICE_PREVIEW_REGION, _preview)
    hass.services.async_register(DOMAIN, SERVICE_RESET_CANS, _reset)
