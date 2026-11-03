from homeassistant.components.button import ButtonEntity
from .entity import IntiHAEntity, discover


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    async_add_entities([ControlButton(c, key, label) for key, label in (
        ("scan", "Search devices"), ("stop_all", "Stop all"),
        ("reconnect", "Reconnect server"), ("cancel_probe", "Cancel proxy probe"))])
    def factory(d):
        yield (d.key, "stop"), ControlButton(c, "stop", "Stop", d.key)
        if d.description.battery_index is not None:
            yield (d.key, "battery"), ControlButton(c, "refresh_battery", "Refresh battery", d.key)
    discover(c, entry, async_add_entities, factory)


class ControlButton(IntiHAEntity, ButtonEntity):
    def __init__(self, c, key, label, device_key=None):
        super().__init__(c, key, label, device_key)
        self.key = key
        self._attr_icon = "mdi:stop-circle-outline" if "stop" in key else "mdi:refresh"

    @property
    def available(self):
        return self.device_key is None or bool(self.controller.connected and self.device and self.device.connected)

    async def async_press(self):
        operations = {"scan": self.controller.scan, "stop_all": self.controller.stop_all,
            "reconnect": self.controller.reconnect, "cancel_probe": self.coordinator.probe.cancel}
        if self.key == "stop":
            await self.perform(self.controller.stop_device(self.device_key))
        elif self.key == "refresh_battery":
            await self.perform(self.controller.read_battery(self.device_key))
        else:
            await self.perform(operations[self.key]())
        self.coordinator.updated()
