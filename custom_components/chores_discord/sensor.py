"""Sensors: overdue chores and what the camera last saw at the curb."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import trash
from .coordinator import ChoresConfigEntry
from .entity import ChoresEntity

_CANS_STATES = [trash.OUT, trash.NOT_OUT, trash.UNSURE, trash.DARK, trash.UNKNOWN]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChoresConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [OverdueSensor(coordinator, entry, "overdue")]
    if coordinator.cans_enabled:
        entities.append(CansSensor(coordinator, entry, "trash_cans"))
    async_add_entities(entities)


class OverdueSensor(ChoresEntity, SensorEntity):
    """How many chores are overdue; their names as an attribute."""

    _attr_native_unit_of_measurement = "chores"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.overdue())

    @property
    def extra_state_attributes(self) -> dict:
        return {"chores": [c.name for c in self.coordinator.overdue()]}


class CansSensor(ChoresEntity, SensorEntity):
    """out / not_out / unsure / dark / unknown, from the latest look."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _CANS_STATES

    @property
    def native_value(self) -> str:
        last = self.coordinator.state["cans"].get("last") or {}
        return last.get("result", trash.UNKNOWN)

    @property
    def extra_state_attributes(self) -> dict:
        return self.coordinator.cans_attributes()
