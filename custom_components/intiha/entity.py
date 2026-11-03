"""Push-only entities, with master controls independent of engine availability."""
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, VERSION


class IntiHAEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, key, name, device_key=None):
        self.coordinator = coordinator
        self.device_key = device_key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{device_key or 'server'}_{key}"

    @property
    def controller(self):
        return self.coordinator.controller

    @property
    def device(self):
        return self.controller.devices.get(self.device_key)

    @property
    def device_info(self):
        entry_id = self.coordinator.entry.entry_id
        if self.device_key is None:
            return DeviceInfo(identifiers={(DOMAIN, entry_id)}, name="IntiHA",
                manufacturer="IntiHA", model="Native Intiface client", sw_version=VERSION)
        return DeviceInfo(identifiers={(DOMAIN, f"{entry_id}:native:{self.device_key}")},
            name=self.device.description.label if self.device else "Intiface device",
            via_device=(DOMAIN, entry_id))

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.add_listener(self.async_write_ha_state))

    async def perform(self, coroutine):
        try:
            return await coroutine
        except Exception as err:
            raise HomeAssistantError(str(err)) from err


def discover(coordinator, entry, async_add_entities, factory):
    added = set()
    def refresh():
        entities = []
        for device in tuple(coordinator.controller.devices.values()):
            for identifier, entity in factory(device):
                if identifier not in added:
                    added.add(identifier)
                    entities.append(entity)
        if entities:
            async_add_entities(entities)
    entry.async_on_unload(coordinator.add_listener(refresh))
    refresh()
