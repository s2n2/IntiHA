"""Connection and scanning statuses remain available when offline."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import IntiHAEntity, async_add_discovered_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = [
        IntiHABinarySensor(coordinator, key)
        for key in ("engine_connected", "client_connected", "scanning", "stop_unconfirmed")
    ]
    async_add_entities(entities)
    async_add_discovered_entities(
        coordinator,
        entry,
        async_add_entities,
        lambda address: [IntiHABinarySensor(coordinator, "connected", address)],
    )


class IntiHABinarySensor(IntiHAEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, address=None):
        super().__init__(coordinator, key, address)
        self.key = key
        if key in ("engine_connected", "client_connected", "connected"):
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        elif key == "stop_unconfirmed":
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        else:
            self._attr_icon = "mdi:bluetooth-search"

    @property
    def is_on(self) -> bool:
        if self.address:
            return self.coordinator.devices[self.address].connected
        if self.key == "stop_unconfirmed":
            return self.coordinator.stop_uncertain
        return bool(getattr(self.coordinator, self.key))
