"""Capability-based vibration controls; values are last commands, not feedback."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import callback

from .entity import IntiHAEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    added: set[tuple[str, int]] = set()

    @callback
    def add_discovered_controls():
        entities = []
        for address, device in coordinator.devices.items():
            for index, feature in enumerate(device.scalar_features()):
                if feature.get("ActuatorType") != "Vibrate" or (address, index) in added:
                    continue
                added.add((address, index))
                entities.append(IntiHALevel(coordinator, address, index, feature))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.add_listener(add_discovered_controls))
    add_discovered_controls()


class IntiHALevel(IntiHAEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:vibrate"

    def __init__(self, coordinator, address, index, feature):
        super().__init__(coordinator, f"level_{index}", address)
        self.index = index
        self._attr_translation_key = "level"
        self._attr_translation_placeholders = {"number": str(index + 1)}
        steps = max(1, int(feature.get("StepCount", 100)))
        self._attr_native_step = round(100 / steps, 6)

    @property
    def available(self) -> bool:
        coordinator = self.coordinator
        return (
            coordinator.enabled
            and not coordinator._control_paused
            and coordinator.engine_connected
            and coordinator.devices[self.address].connected
            and not coordinator.client_connected
        )

    @property
    def native_value(self) -> float | None:
        # None avoids falsely reporting a physical motor speed after a stop,
        # engine restart, external takeover, or uncertain device state.
        return self.coordinator.devices[self.address].outputs.get(self.index)

    @property
    def extra_state_attributes(self):
        return {
            "value_type": "last_command",
            "auto_stop_seconds": self.coordinator.config.get("control_timeout", 60),
        }

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_output(self.address, self.index, value)
