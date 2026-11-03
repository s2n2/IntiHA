from homeassistant.components.number import NumberEntity, NumberMode
from .entity import IntiHAEntity, discover


async def async_setup_entry(hass, entry, async_add_entities):
    c = entry.runtime_data
    def factory(device):
        for motor in device.description.motors:
            yield (device.key, motor.index), Vibration(c, device.key, motor)
    discover(c, entry, async_add_entities, factory)


class Vibration(IntiHAEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:vibrate"

    def __init__(self, c, device_key, motor):
        super().__init__(c, f"vibration_{motor.index}", motor.name, device_key)
        self.motor_index = motor.index
        self._attr_native_step = max(0.01, 100 / motor.steps)

    @property
    def available(self):
        d = self.device
        return bool(self.controller.connected and d and d.connected and d.ready and not d.ambiguous
                    and any(m.index == self.motor_index for m in d.description.motors))

    @property
    def native_value(self):
        return self.device.values.get(self.motor_index) if self.device else None

    async def async_set_native_value(self, value):
        await self.perform(self.controller.set_vibration(self.device_key, self.motor_index, value))
