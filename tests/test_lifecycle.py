"""Load the complete integration and actual HA entity platforms offline."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiha.const import DOMAIN
from custom_components.intiha.diagnostics import async_get_config_entry_diagnostics

ADDRESS = "AA:BB:CC:DD:EE:01"
FEATURE = {"ActuatorType": "Vibrate", "StepCount": 15, "FeatureDescriptor": "Motor"}


@pytest.fixture
async def loaded(hass, mock_bluetooth, hass_storage, request):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IntiHA",
        unique_id=DOMAIN,
        data={"engine_url": "ws://engine.example:12346/bridge"},
    )
    if stored := getattr(request, "param", None):
        hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
            "version": 1,
            "data": stored,
        }
    entry.add_to_hass(hass)
    with (
        patch("custom_components.intiha.coordinator.BleBridge.async_start", new_callable=AsyncMock),
        patch(
            "custom_components.intiha.coordinator.BleBridge.async_disconnect_all",
            new_callable=AsyncMock,
        ),
        patch("custom_components.intiha.coordinator.BleBridge.async_stop", new_callable=AsyncMock),
        patch("custom_components.intiha.coordinator.EngineBridge.run", new_callable=AsyncMock),
        patch("custom_components.intiha.coordinator.bluetooth.async_scanner_count", return_value=2),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry
        if entry.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


def entity_id(hass, entry, domain, key, address=None):
    return er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{address or 'server'}_{key}"
    )


async def discover(coordinator, address=ADDRESS, name="Advertising name"):
    await coordinator._async_engine_message(
        {"type": "discovered", "address": address, "name": name, "protocols": ["wevibe"]},
        coordinator.bridge.epoch,
    )


async def ready(coordinator, name="Test device"):
    await coordinator._async_engine_message(
        {
            "type": "devices",
            "devices": [
                {
                    "address": ADDRESS,
                    "connection_id": "new-connection",
                    "ready": True,
                    "DeviceIndex": 2,
                    "DeviceName": name,
                    "DeviceMessages": {"ScalarCmd": [FEATURE]},
                }
            ],
        },
        coordinator.bridge.epoch,
    )


async def test_entry_loads_with_offline_engine_and_controls_stay_available(hass, loaded):
    assert loaded.state is ConfigEntryState.LOADED
    switch = entity_id(hass, loaded, "switch", "enabled")
    status = entity_id(hass, loaded, "sensor", "status")
    connected = entity_id(hass, loaded, "binary_sensor", "engine_connected")
    assert hass.states.get(switch).state == "on"
    assert hass.states.get(status).state == "connecting"
    assert hass.states.get(connected).state == "off"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert hass.states.get(status).state == "disabled"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "on"
    assert hass.states.get(status).state == "connecting"


async def test_capability_creates_actual_number_entity_and_owner_gates_it(hass, loaded):
    coordinator = loaded.runtime_data
    coordinator.engine_connected = True
    await discover(coordinator)
    await ready(coordinator)
    await hass.async_block_till_done()
    number = entity_id(hass, loaded, "number", "level_0", ADDRESS)
    assert number is not None
    assert hass.states.get(number).state != STATE_UNAVAILABLE
    assert hass.states.get(number).attributes["value_type"] == "last_command"
    await coordinator._async_engine_message(
        {"type": "status", "client_connected": True}, coordinator.bridge.epoch
    )
    assert hass.states.get(number).state == STATE_UNAVAILABLE
    stop = entity_id(hass, loaded, "button", "stop", ADDRESS)
    assert hass.states.get(stop).state != STATE_UNAVAILABLE


async def test_devices_and_controls_appear_once_without_reload(hass, loaded):
    coordinator = loaded.runtime_data
    assert entity_id(hass, loaded, "binary_sensor", "connected", ADDRESS) is None
    for _ in range(3):
        await discover(coordinator)
    await hass.async_block_till_done()
    keys = {
        "binary_sensor": ("connected",),
        "button": ("stop",),
        "switch": ("connect_enabled",),
        "sensor": ("device_status", "battery", "signal_strength", "last_seen_via", "last_seen"),
    }
    for domain, domain_keys in keys.items():
        for key in domain_keys:
            assert hass.states.get(entity_id(hass, loaded, domain, key, ADDRESS)) is not None
    assert entity_id(hass, loaded, "number", "level_0", ADDRESS) is None
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, f"{loaded.entry_id}:{ADDRESS}"), loaded.entry_id
    )
    assert device.name == "Advertising name"
    registry.async_update_device(device.id, name_by_user="My device")
    coordinator.engine_connected = True
    for _ in range(3):
        await ready(coordinator, name="Official device name")
    await hass.async_block_till_done()
    assert entity_id(hass, loaded, "number", "level_0", ADDRESS) is not None
    device = registry.async_get(device.id)
    assert device.name == "Official device name"
    assert device.name_by_user == "My device"
    entries = er.async_entries_for_device(er.async_get(hass), device.id)
    assert len(entries) == 9
    await discover(coordinator, address="AA:BB:CC:DD:EE:02", name="Another device")
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity_id(hass, loaded, "button", "stop", "AA:BB:CC:DD:EE:02")) is not None
    )
    assert loaded.state is ConfigEntryState.LOADED


async def test_diagnostics_do_not_expose_addresses_names_or_activity(hass, loaded):
    await discover(loaded.runtime_data, name="Private device name")
    loaded.runtime_data.devices[ADDRESS].outputs[0] = 47.25
    data = await async_get_config_entry_diagnostics(hass, loaded)
    text = repr(data)
    assert ADDRESS not in text
    assert "engine.example" not in text
    assert "Private device name" not in text
    assert "47.25" not in text
    assert data["known_device_count"] == 1
    assert data["active_connectable_scanners"] == 2


@pytest.mark.parametrize(
    "loaded",
    [
        {
            "enabled": False,
            "devices": {
                ADDRESS: {"name": "Remembered device", "messages": {"ScalarCmd": [FEATURE]}}
            },
        }
    ],
    indirect=True,
)
async def test_remembered_devices_load_offline_and_explicit_off_is_preserved(hass, loaded):
    assert hass.states.get(entity_id(hass, loaded, "switch", "enabled")).state == "off"
    assert entity_id(hass, loaded, "sensor", "battery", ADDRESS) is not None
    assert entity_id(hass, loaded, "binary_sensor", "connected", ADDRESS) is not None
    assert entity_id(hass, loaded, "button", "stop", ADDRESS) is not None
    number = entity_id(hass, loaded, "number", "level_0", ADDRESS)
    assert number is not None
    assert hass.states.get(number).state == STATE_UNAVAILABLE


async def test_unload_closes_ble_and_detaches_entities(hass, loaded):
    coordinator = loaded.runtime_data
    await discover(coordinator)
    await ready(coordinator)
    await hass.async_block_till_done()
    stop = entity_id(hass, loaded, "button", "stop", ADDRESS)
    assert await hass.config_entries.async_unload(loaded.entry_id)
    await hass.async_block_till_done()
    assert loaded.state is ConfigEntryState.NOT_LOADED
    coordinator.ble.async_stop.assert_awaited_once()
    assert coordinator._shutdown
    assert not coordinator._listeners
    assert hass.states.get(stop).state == STATE_UNAVAILABLE
