from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from .entity import IntiHAEntity, discover


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([Connected(coordinator, "engine_connected", "Engine connected")])
    discover(coordinator, entry, async_add_entities, lambda d: [
        (d.key, Connected(coordinator, "connected", "Connected", d.key))])


class Connected(IntiHAEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self):
        if self.device_key is None:
            return self.controller.connected
        return bool(self.controller.connected and self.device and self.device.connected)
