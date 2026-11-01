"""Search, reconnect and stop controls."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import IntiHAEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = [IntiHAButton(coordinator, key) for key in ("search", "stop_all", "reconnect")]
    entities.extend(
        IntiHAButton(coordinator, "stop", address) for address in sorted(coordinator.selected)
    )
    async_add_entities(entities)


class IntiHAButton(IntiHAEntity, ButtonEntity):
    def __init__(self, coordinator, key, address=None):
        super().__init__(coordinator, key, address)
        self.key = key
        self._attr_icon = {
            "search": "mdi:bluetooth-search",
            "stop_all": "mdi:stop-circle",
            "stop": "mdi:stop-circle",
            "reconnect": "mdi:connection",
        }[key]
        if key in ("search", "reconnect"):
            self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self):
        if self.key == "search":
            await self.coordinator.async_scan()
        elif self.key == "stop_all":
            await self.coordinator.async_stop_all()
        elif self.key == "reconnect":
            await self.coordinator.async_reconnect()
        else:
            await self.coordinator.async_device_stop(self.address)
