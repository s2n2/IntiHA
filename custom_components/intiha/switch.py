from homeassistant.components.switch import SwitchEntity
from .entity import IntiHAEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Enabled(entry.runtime_data, "enabled", "Enabled")])


class Enabled(IntiHAEntity, SwitchEntity):
    _attr_icon = "mdi:bluetooth-connect"

    @property
    def is_on(self):
        return self.controller.enabled

    async def async_turn_on(self, **kwargs):
        await self.perform(self.coordinator.set_enabled(True))

    async def async_turn_off(self, **kwargs):
        await self.perform(self.coordinator.set_enabled(False))
