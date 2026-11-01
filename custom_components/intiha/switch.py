"""Always-available master switch."""

from homeassistant.components.switch import SwitchEntity

from .entity import IntiHAEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([IntiHASwitch(entry.runtime_data)])


class IntiHASwitch(IntiHAEntity, SwitchEntity):
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator):
        super().__init__(coordinator, "enabled")

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_enabled(False)
