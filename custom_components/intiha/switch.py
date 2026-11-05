"""Server and per-device connection switches."""

from homeassistant.components.switch import SwitchEntity

from .entity import IntiHAEntity, async_add_discovered_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([IntiHASwitch(coordinator)])
    async_add_discovered_entities(
        coordinator,
        entry,
        async_add_entities,
        lambda address: (IntiHADeviceConnectSwitch(coordinator, address),),
    )


class IntiHASwitch(IntiHAEntity, SwitchEntity):
    """Master server enable control."""

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


class IntiHADeviceConnectSwitch(IntiHAEntity, SwitchEntity):
    """Persist whether IntiHA may automatically connect to one BLE device."""

    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator, address):
        super().__init__(coordinator, "connect_enabled", address)

    @property
    def is_on(self) -> bool:
        return self.coordinator.devices[self.address].connect_enabled

    @property
    def icon(self) -> str:
        return "mdi:bluetooth-connect" if self.is_on else "mdi:bluetooth-off"

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_device_connect(self.address, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_device_connect(self.address, False)
