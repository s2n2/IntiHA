"""Per-device commanded intensity telemetry, without modifying public Buttplug messages."""

from homeassistant.components.sensor import SensorEntity
from custom_components.intiha.sensor import IntiHASensor
ADDRESS = "AA:BB:CC:DD:EE:01"


def device_description():
    return {
        "address": ADDRESS, "connection_id": "connection-a",
        "ready": True, "DeviceIndex": 3, "DeviceName": "Test device",
        "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Vibrate", "StepCount": 15}]},
    }


async def test_output_telemetry_follows_external_commands_and_session(coordinator):
    coordinator.engine_connected = True
    coordinator._update_devices([device_description()])
    observed = []
    coordinator.add_intensity_listener(ADDRESS, lambda: observed.append(True))

    await coordinator._async_engine_message({
        "type": "status", "client_connected": True,
    }, coordinator.bridge.epoch)
    data = {
        "type": "output_level", "address": ADDRESS, "connection_id": "connection-a",
        "levels": [{"index": 0, "percent": 35}, {"index": 1, "percent": 80}],
    }
    await coordinator._async_engine_message(data, coordinator.bridge.epoch)
    assert coordinator.devices[ADDRESS].outputs == {0: 35, 1: 80}
    assert observed == [True]
    sensor = IntiHASensor(coordinator, "intensity", ADDRESS)
    assert isinstance(sensor, SensorEntity)
    assert sensor.native_value == 80
    assert sensor.extra_state_attributes["vibration_channels"] == {0: 35, 1: 80}
    assert sensor.extra_state_attributes["hardware_feedback"] is False

    # Repeated status ticks cannot erase a live external client's reading.
    await coordinator._async_engine_message({
        "type": "status", "client_connected": True,
    }, coordinator.bridge.epoch)
    assert sensor.native_value == 80

    # Commands for a retired Bluetooth session must never update the sensor.
    stale = {**data, "connection_id": "retired", "levels": [{"index": 0, "percent": 99}]}
    await coordinator._async_engine_message(stale, coordinator.bridge.epoch)
    assert sensor.native_value == 80
    assert observed == [True]

    # A verified stop clears the commanded setpoint.
    await coordinator._async_engine_message({"type": "output_clear", "address": ADDRESS}, coordinator.bridge.epoch)
    assert sensor.native_value == 0


async def test_telemetry_unavailable_on_disconnect_and_bad_values_rejected(coordinator):
    coordinator.engine_connected = True
    coordinator._update_devices([device_description()])
    sensor = IntiHASensor(coordinator, "intensity", ADDRESS)
    assert sensor.available
    await coordinator._async_engine_message({
        "type": "output_level", "address": ADDRESS, "connection_id": "connection-a",
        "levels": [{"index": 0, "percent": 35}, {"index": 1, "percent": 150},
                   {"index": -1, "percent": 99}, {"index": 2, "percent": "invalid"}],
    }, coordinator.bridge.epoch)
    assert sensor.native_value == 35
    coordinator._update_devices([])
    assert not sensor.available
    assert coordinator.devices[ADDRESS].outputs == {}


async def test_control_owner_status_clears_old_manual_values_once(coordinator):
    coordinator.engine_connected = True
    coordinator._update_devices([device_description()])
    device = coordinator.devices[ADDRESS]
    device.outputs[0] = 70
    await coordinator._async_engine_message({"type": "status", "client_connected": True}, coordinator.bridge.epoch)
    assert device.outputs == {}
    device.outputs[0] = 40
    await coordinator._async_engine_message({"type": "status", "client_connected": True}, coordinator.bridge.epoch)
    assert device.outputs[0] == 40
