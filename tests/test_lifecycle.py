"""Load the complete integration and actual HA entity platforms offline."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiha.const import DOMAIN
from custom_components.intiha.diagnostics import async_get_config_entry_diagnostics

ADDRESS = "AA:BB:CC:DD:EE:01"
TOKEN = "a" * 64


@pytest.fixture
async def loaded(hass, mock_bluetooth):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IntiHA",
        unique_id=DOMAIN,
        data={"engine_url": "ws://engine.example:12346/bridge", "bridge_token": TOKEN},
        options={"selected_devices": [ADDRESS]},
    )
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


async def test_entry_loads_with_offline_engine_and_controls_stay_available(hass, loaded):
    assert loaded.state is ConfigEntryState.LOADED
    switch = entity_id(hass, loaded, "switch", "enabled")
    status = entity_id(hass, loaded, "sensor", "status")
    connected = entity_id(hass, loaded, "binary_sensor", "engine_connected")
    assert hass.states.get(switch).state == "off"
    assert hass.states.get(status).state == "disabled"
    assert hass.states.get(connected).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "on"
    assert hass.states.get(status).state == "connecting"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert hass.states.get(status).state == "disabled"


async def test_capability_creates_actual_number_entity_and_owner_gates_it(hass, loaded):
    coordinator = loaded.runtime_data
    coordinator.enabled = True
    coordinator.engine_connected = True
    await coordinator._async_engine_message(
        {
            "type": "devices",
            "devices": [
                {
                    "address": ADDRESS,
                    "connection_id": "new-connection",
                    "ready": True,
                    "DeviceIndex": 2,
                    "DeviceName": "Test device",
                    "DeviceMessages": {
                        "ScalarCmd": [
                            {
                                "ActuatorType": "Vibrate",
                                "StepCount": 15,
                                "FeatureDescriptor": "Motor",
                            }
                        ],
                    },
                }
            ],
        },
        coordinator.bridge.epoch,
    )
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


async def test_diagnostics_do_not_expose_secrets_addresses_or_activity(hass, loaded):
    data = await async_get_config_entry_diagnostics(hass, loaded)
    text = repr(data)
    assert TOKEN not in text
    assert ADDRESS not in text
    assert "engine.example" not in text
    assert data["selected_device_count"] == 1
    assert data["active_connectable_scanners"] == 2


async def test_unload_closes_ble_and_removes_entities(hass, loaded):
    coordinator = loaded.runtime_data
    assert await hass.config_entries.async_unload(loaded.entry_id)
    await hass.async_block_till_done()
    assert loaded.state is ConfigEntryState.NOT_LOADED
    coordinator.ble.async_stop.assert_awaited_once()
    assert coordinator._shutdown
