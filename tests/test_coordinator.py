"""Exercise user-visible state and races across real coordinator methods."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiha.bridge import BridgeError
from custom_components.intiha.const import (
    CONF_AUTO_RECONNECT,
    CONF_CONTROL_TIMEOUT,
    CONF_ENGINE_URL,
    CONF_SCAN_DURATION,
    DOMAIN,
)
from custom_components.intiha.coordinator import IntiHACoordinator

ADDRESS = "AA:BB:CC:DD:EE:01"
OTHER_ADDRESS = "AA:BB:CC:DD:EE:02"


def device_description(
    address: str = ADDRESS,
    *,
    connection_id: str = "connection-a",
    index: int = 3,
    ready: bool = True,
) -> dict:
    return {
        "address": address,
        "connection_id": connection_id,
        "ready": ready,
        "DeviceIndex": index,
        "DeviceName": "Test device",
        "DeviceMessages": {
            "ScalarCmd": [
                {
                    "FeatureDescriptor": "Vibration",
                    "ActuatorType": "Vibrate",
                    "StepCount": 15,
                }
            ]
        },
    }


@pytest.fixture
async def coordinator(hass):
    """Use the real coordinator with controllable network/hardware boundaries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IntiHA",
        data={
            CONF_ENGINE_URL: "ws://engine.example:12346/bridge",
            CONF_AUTO_RECONNECT: True,
            CONF_CONTROL_TIMEOUT: 60,
            CONF_SCAN_DURATION: 30,
        },
    )
    entry.add_to_hass(hass)
    value = IntiHACoordinator(hass, entry)
    value.bridge = SimpleNamespace(
        connected=False,
        epoch=5,
        request=AsyncMock(return_value={}),
        send=AsyncMock(),
        reconnect=AsyncMock(),
        close=AsyncMock(),
    )
    value.ble = SimpleNamespace(
        async_start=AsyncMock(),
        async_stop=AsyncMock(),
        async_disconnect_all=AsyncMock(),
        async_request=AsyncMock(return_value={}),
        discovered=Mock(return_value=[]),
    )
    value._store = SimpleNamespace(
        async_load=AsyncMock(return_value=None),
        async_save=AsyncMock(),
        async_delay_save=Mock(),
    )
    return value


async def test_initialise_offline_requires_no_engine_and_starts_enabled(coordinator):
    await coordinator.async_initialize()
    assert coordinator.enabled
    assert coordinator.status == "connecting"
    assert not coordinator.devices
    assert not coordinator.engine_connected
    coordinator.ble.async_start.assert_awaited_once()
    coordinator.bridge.request.assert_not_awaited()


async def test_enabling_offline_records_intent_without_failing_setup(coordinator):
    await coordinator.async_set_enabled(True)
    assert coordinator.enabled
    assert coordinator.status == "connecting"
    coordinator.bridge.request.assert_not_awaited()
    coordinator._store.async_delay_save.assert_called()


async def test_disable_offline_still_closes_ble_and_clears_outputs(coordinator):
    coordinator.enabled = True
    coordinator._update_devices([device_description()])
    coordinator.devices[ADDRESS].outputs[0] = 60
    coordinator.scanning = True
    coordinator._scan_until = 1000

    await coordinator.async_set_enabled(False)

    coordinator.ble.async_disconnect_all.assert_awaited_once()
    assert not coordinator.enabled
    assert not coordinator.scanning
    assert coordinator._scan_until == 0
    assert not coordinator.devices[ADDRESS].connected
    assert coordinator.devices[ADDRESS].outputs == {}


async def test_disable_during_engine_failure_still_releases_local_ble(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.bridge.connected = True
    coordinator.bridge.request.side_effect = BridgeError("Engine session ended")
    coordinator._update_devices([device_description()])

    await coordinator.async_set_enabled(False)

    coordinator.ble.async_disconnect_all.assert_awaited_once()
    assert not coordinator.enabled
    assert not coordinator.devices[ADDRESS].connected
    assert coordinator.last_error == "Engine session ended"


@pytest.mark.parametrize("operation", ["off", "stop"])
async def test_local_stop_precedes_waiting_for_engine_reply(coordinator, operation):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.bridge.connected = True
    coordinator._update_devices([device_description()])
    sequence = []

    async def local_cleanup():
        sequence.append("local_cleanup")

    async def remote_request(op, **_kwargs):
        sequence.append(op)
        assert sequence[0] == "local_cleanup"
        return {}

    coordinator.ble.async_disconnect_all.side_effect = local_cleanup
    coordinator.bridge.request.side_effect = remote_request

    if operation == "off":
        await coordinator.async_set_enabled(False)
        assert sequence == ["local_cleanup", "configure"]
    else:
        await coordinator.async_stop_all()
        assert sequence == ["local_cleanup", "stop"]


async def test_stop_blocks_new_ble_connections_while_engine_reply_stalls(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.bridge.connected = True
    coordinator._update_devices([device_description()])
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_stop(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {}

    coordinator.bridge.request.side_effect = delayed_stop
    task = asyncio.create_task(coordinator.async_stop_all())
    try:
        await asyncio.wait_for(started.wait(), 1)
        coordinator.ble.async_disconnect_all.assert_awaited_once()
        await coordinator._async_ble_request(
            {
                "type": "request",
                "id": 10,
                "op": "connect",
                "address": ADDRESS,
                "connection_id": "new-during-stop",
            },
            coordinator.bridge.epoch,
        )
        coordinator.ble.async_request.assert_not_awaited()
        coordinator.bridge.send.assert_awaited()
        assert coordinator.bridge.send.await_args.args[0]["ok"] is False
        release.set()
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_enabling_configures_only_and_never_replays_prior_output(coordinator):
    coordinator.bridge.connected = True
    coordinator._update_devices([device_description()])
    coordinator.devices[ADDRESS].outputs[0] = 75

    await coordinator.async_set_enabled(True)

    coordinator.bridge.request.assert_awaited_once_with(
        "configure",
        enabled=True,
        auto_reconnect=True,
        manual_timeout_seconds=60,
    )
    settings = coordinator._hello()
    assert "outputs" not in settings
    persisted = coordinator._persisted_state()
    assert "outputs" not in persisted["devices"][ADDRESS]
    assert "connection_id" not in persisted["devices"][ADDRESS]


async def test_stop_failure_is_visible_and_attempts_local_cleanup(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.bridge.connected = True
    coordinator.bridge.request.side_effect = BridgeError("Stop failed")
    coordinator._update_devices([device_description()])

    with pytest.raises(HomeAssistantError, match="Stop could not be confirmed"):
        await coordinator.async_stop_all()

    assert coordinator.stop_uncertain
    coordinator.ble.async_disconnect_all.assert_awaited_once()


async def test_stop_failed_ble_event_does_not_claim_confirmed_stop(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    await coordinator._async_ble_event(
        {
            "type": "stop_failed",
            "address": ADDRESS,
            "connection_id": "connection-a",
        }
    )
    assert coordinator.stop_uncertain
    assert "could not be confirmed" in coordinator.last_error
    coordinator.bridge.send.assert_not_awaited()


async def test_stop_failure_event_during_admin_reply_is_not_erased(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.bridge.connected = True

    async def failed_stop_before_success_reply(*_args, **_kwargs):
        await coordinator._async_ble_event(
            {
                "type": "stop_failed",
                "address": ADDRESS,
                "connection_id": "connection-a",
            }
        )
        # Upstream Buttplug can acknowledge a stop even if the hardware task
        # times out. A real local failure observed meanwhile must remain visible.
        return {}

    coordinator.bridge.request.side_effect = failed_stop_before_success_reply
    with pytest.raises(HomeAssistantError, match="Stop could not be confirmed"):
        await coordinator.async_stop_all()
    assert coordinator.stop_uncertain
    assert "could not be confirmed" in coordinator.last_error


async def test_empty_stop_does_not_clear_an_unreachable_devices_failure(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.bridge.connected = True
    await coordinator._async_ble_event(
        {
            "type": "stop_failed",
            "address": ADDRESS,
            "connection_id": "lost-connection",
        }
    )

    # The engine has no live session for the failed device, so stopping its
    # empty inventory succeeds without delivering anything to that device.
    await coordinator.async_stop_all()

    assert coordinator.stop_uncertain


async def test_new_ready_connection_clears_only_its_prior_stop_failure(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    for address in (ADDRESS, OTHER_ADDRESS):
        await coordinator._async_ble_event(
            {"type": "stop_failed", "address": address, "connection_id": "old"}
        )

    coordinator._update_devices([device_description(connection_id="new")])
    assert coordinator.stop_uncertain
    assert coordinator._persisted_state()["uncertain_addresses"] == [OTHER_ADDRESS]

    coordinator._update_devices(
        [
            device_description(connection_id="new"),
            device_description(OTHER_ADDRESS, connection_id="other-new", index=4),
        ]
    )
    assert not coordinator.stop_uncertain
    assert coordinator._persisted_state()["uncertain_addresses"] == []


async def test_unconfirmed_stop_survives_restart_without_restoring_output(coordinator):
    coordinator._store.async_load.return_value = {
        "enabled": True,
        "stop_uncertain": True,
        "uncertain_addresses": [ADDRESS],
        "devices": {
            ADDRESS: {
                "name": "Test device",
                "messages": device_description()["DeviceMessages"],
            }
        },
    }

    await coordinator.async_initialize()

    assert coordinator.status == "stop_unconfirmed"
    assert coordinator.stop_uncertain
    assert coordinator._persisted_state()["uncertain_addresses"] == [ADDRESS]
    assert coordinator.devices[ADDRESS].outputs == {}
    assert not coordinator.devices[ADDRESS].connected
    coordinator.bridge.request.assert_not_awaited()


async def test_unready_device_does_not_enable_controls(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator._update_devices([device_description(ready=False)])

    with pytest.raises(HomeAssistantError, match="offline|disabled|ready"):
        await coordinator.async_set_output(ADDRESS, 0, 25)

    coordinator.bridge.request.assert_not_awaited()


async def test_external_owner_blocks_motion_but_allows_stop(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.client_connected = True
    coordinator.bridge.connected = True
    coordinator.bridge.request.return_value = {"message": {"Ok": {"Id": 1}}}
    coordinator._update_devices([device_description()])

    with pytest.raises(HomeAssistantError, match="external client"):
        await coordinator.async_set_output(ADDRESS, 0, 25)
    coordinator.bridge.request.assert_not_awaited()

    await coordinator.async_device_stop(ADDRESS)
    coordinator.bridge.request.assert_awaited_once_with(
        "command", message={"StopDeviceCmd": {"Id": 1, "DeviceIndex": 3}}
    )


async def test_old_disconnect_cannot_clear_a_new_connection(coordinator):
    coordinator.engine_connected = True
    coordinator._update_devices([device_description(connection_id="new")])
    device = coordinator.devices[ADDRESS]
    device.outputs[0] = 20

    await coordinator._async_ble_event(
        {
            "type": "disconnected",
            "address": ADDRESS,
            "connection_id": "old",
            "reason": "old callback",
        }
    )

    assert device.connected
    assert device.connection_id == "new"
    assert device.outputs == {0: 20}


async def test_matching_disconnect_clears_commanded_state(coordinator):
    coordinator.engine_connected = True
    coordinator._update_devices([device_description(connection_id="current")])
    device = coordinator.devices[ADDRESS]
    device.outputs[0] = 20

    await coordinator._async_ble_event(
        {
            "type": "disconnected",
            "address": ADDRESS,
            "connection_id": "current",
            "reason": "link_lost",
        }
    )

    assert not device.connected
    assert device.index is None
    assert device.outputs == {}


async def test_late_command_reply_cannot_restore_output_after_reconnect(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.bridge.connected = True
    coordinator._update_devices([device_description(connection_id="old")])
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_command(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {"message": {"Ok": {"Id": 1}}}

    coordinator.bridge.request.side_effect = delayed_command
    task = asyncio.create_task(coordinator.async_set_output(ADDRESS, 0, 80))
    try:
        await asyncio.wait_for(started.wait(), 1)
        # The engine can reuse a DeviceIndex. Only connection_id distinguishes
        # these two physical links while the bridge itself stays connected.
        coordinator._update_devices([device_description(connection_id="new")])
        release.set()
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert coordinator.devices[ADDRESS].connection_id == "new"
    assert coordinator.devices[ADDRESS].outputs == {}


async def test_engine_output_clear_event_clears_only_requested_device(coordinator):
    coordinator._update_devices([device_description(), device_description(OTHER_ADDRESS, index=4)])
    coordinator.devices[ADDRESS].outputs[0] = 80
    coordinator.devices[OTHER_ADDRESS].outputs[0] = 40

    await coordinator._async_engine_message(
        {"type": "output_clear", "address": ADDRESS}, coordinator.bridge.epoch
    )
    assert coordinator.devices[ADDRESS].outputs == {}
    assert coordinator.devices[OTHER_ADDRESS].outputs == {0: 40}

    await coordinator._async_engine_message({"type": "output_clear"}, coordinator.bridge.epoch)
    assert coordinator.devices[OTHER_ADDRESS].outputs == {}


async def test_bridge_loss_cancels_pending_hardware_and_clears_state(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator.client_connected = True
    coordinator.scanning = True
    coordinator._update_devices([device_description()])
    coordinator.devices[ADDRESS].outputs[0] = 80
    waiting = asyncio.create_task(asyncio.Event().wait())
    coordinator._request_tasks.add(waiting)

    await coordinator._async_disconnected("Cannot reach engine")

    assert waiting.cancelled()
    assert not coordinator.engine_connected
    assert not coordinator.client_connected
    assert not coordinator.scanning
    assert coordinator.devices[ADDRESS].outputs == {}
    assert not coordinator.devices[ADDRESS].connected
    coordinator.ble.async_disconnect_all.assert_awaited_once()


async def test_old_generation_hardware_request_never_touches_ble(coordinator):
    coordinator.enabled = True
    await coordinator._async_ble_request(
        {
            "type": "request",
            "id": 1,
            "op": "write",
            "address": ADDRESS,
            "connection_id": "old",
            "characteristic": "00000000-0000-0000-0000-000000000001",
            "data": [42],
            "response": True,
        },
        coordinator.bridge.epoch - 1,
    )
    coordinator.ble.async_request.assert_not_awaited()
    coordinator.bridge.send.assert_not_awaited()


async def test_invalid_percentages_never_reach_engine(coordinator):
    coordinator.enabled = True
    coordinator.engine_connected = True
    coordinator._update_devices([device_description()])
    for value in (float("nan"), float("inf"), -1, 101):
        with pytest.raises(HomeAssistantError, match="between 0 and 100"):
            await coordinator.async_set_output(ADDRESS, 0, value)
    coordinator.bridge.request.assert_not_awaited()


async def test_network_error_redacts_engine_url(coordinator):
    message = coordinator._safe_error("Failed at ws://private-engine/bridge")
    assert "private-engine" not in message
    assert "[engine]" in message


async def test_compatible_discovery_adds_and_persists_device_without_selection(coordinator):
    coordinator.engine_connected = True
    assert not coordinator.devices
    listener = Mock()
    coordinator.add_listener(listener)

    await coordinator._async_engine_message(
        {"type": "discovered", "address": ADDRESS, "name": "Nova", "protocols": ["wevibe"]},
        coordinator.bridge.epoch,
    )

    assert coordinator.devices[ADDRESS].name == "Nova"
    assert not coordinator.devices[ADDRESS].connected
    assert coordinator._persisted_state()["devices"][ADDRESS]["name"] == "Nova"
    assert "selected_addresses" not in coordinator._hello()
    listener.assert_called_once()
    coordinator._store.async_delay_save.assert_called_once()

    coordinator._update_devices([device_description()])
    await coordinator._async_engine_message(
        {"type": "discovered", "address": ADDRESS, "name": "advertising name"},
        coordinator.bridge.epoch,
    )
    assert coordinator.devices[ADDRESS].name == "Test device"
    assert len(coordinator.devices) == 1


async def test_continuous_discovery_forwards_new_advertisements_without_search(coordinator):
    coordinator.engine_connected = True
    assert coordinator._scan_until == 0
    advertisement = {"type": "advertisement", "address": ADDRESS, "name": "Nova"}

    await coordinator._async_ble_event(advertisement)

    coordinator.bridge.send.assert_awaited_once_with(advertisement, epoch=coordinator.bridge.epoch)
    # An advertisement alone does not claim protocol compatibility. The
    # engine sends `discovered` only after the official profiles match it.
    assert not coordinator.devices


async def test_persisted_off_prevents_automatic_discovery_after_restart(coordinator):
    coordinator._store.async_load.return_value = {"enabled": False}
    await coordinator.async_initialize()
    coordinator.engine_connected = True

    await coordinator._async_ble_event({"type": "advertisement", "address": ADDRESS})

    assert not coordinator.enabled
    coordinator.bridge.send.assert_not_awaited()


async def test_rotating_advertisers_do_not_permanently_block_new_devices(coordinator):
    coordinator.engine_connected = True
    coordinator._last_advertisement = {
        f"00:00:00:00:{index // 256:02X}:{index % 256:02X}": float(index) for index in range(512)
    }
    await coordinator._async_ble_event({"type": "advertisement", "address": ADDRESS})

    assert len(coordinator._last_advertisement) == 512
    assert ADDRESS in coordinator._last_advertisement
    coordinator.bridge.send.assert_awaited_once()


async def test_stale_discovery_cannot_add_device_to_new_bridge_generation(coordinator):
    await coordinator._async_engine_message(
        {"type": "discovered", "address": ADDRESS, "name": "stale"},
        coordinator.bridge.epoch - 1,
    )
    assert not coordinator.devices


async def test_new_discovery_during_battery_read_does_not_break_polling(coordinator):
    coordinator.engine_connected = True
    coordinator.bridge.connected = True
    description = device_description()
    description["DeviceMessages"]["SensorReadCmd"] = [{"SensorType": "Battery"}]
    coordinator._update_devices([description])
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_battery(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {"message": {"SensorReading": {"Data": [75]}}}

    coordinator.bridge.request.side_effect = delayed_battery
    poll = asyncio.create_task(coordinator._async_tick(None))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await coordinator._async_engine_message(
            {"type": "discovered", "address": OTHER_ADDRESS, "name": "Another device"},
            coordinator.bridge.epoch,
        )
        release.set()
        await asyncio.wait_for(poll, 1)
    finally:
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)

    assert coordinator.devices[ADDRESS].battery == 75
    assert OTHER_ADDRESS in coordinator.devices


async def test_identified_bond_requests_pairing_before_first_connection(coordinator):
    await coordinator._async_engine_message(
        {
            "type": "discovered",
            "address": ADDRESS,
            "name": "Bond",
            "protocols": ["wevibe-8bit"],
        },
        coordinator.bridge.epoch,
    )
    assert coordinator._requires_pairing(ADDRESS)
    assert coordinator._persisted_state()["devices"][ADDRESS]["pairing_required"] is True


async def test_learned_pairing_requirement_survives_restart(coordinator):
    coordinator._store.async_load.return_value = {
        "devices": {ADDRESS: {"name": "Device", "pairing_required": True}},
    }
    await coordinator.async_initialize()
    assert coordinator._requires_pairing(ADDRESS)


async def test_other_discovered_devices_are_not_forced_to_pair(coordinator):
    await coordinator._async_engine_message(
        {"type": "discovered", "address": ADDRESS, "name": "Nova", "protocols": ["wevibe"]},
        coordinator.bridge.epoch,
    )
    assert not coordinator._requires_pairing(ADDRESS)
    coordinator._require_pairing(ADDRESS)
    assert coordinator._requires_pairing(ADDRESS)
