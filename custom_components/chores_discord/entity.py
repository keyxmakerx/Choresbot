"""Shared base entity for the Chores Discord Bot integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DEVICE_NAME, DOMAIN, SIGNAL_UPDATE
from .coordinator import ChoresConfigEntry, ChoresCoordinator


class ChoresEntity(Entity):
    """Refreshes on the coordinator's dispatcher signal; ids like sensor.chores_*."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: ChoresCoordinator, entry: ChoresConfigEntry, key: str) -> None:
        self.coordinator = coordinator
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer="Chores Discord",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{SIGNAL_UPDATE}_{self._entry.entry_id}", self.async_write_ha_state
            )
        )
