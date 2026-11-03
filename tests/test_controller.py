import asyncio
import time
import pytest
from conftest import device, eventually
from intiha_core.models import DisconnectedError, ProtocolError, ServerError


async def test_auto_scan_and_standard_stop_before_control(native_server, controllers):
    server = await native_server([device()])
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    key = next(iter(ctl.devices))
    await ctl.set_vibration(key, 0, 25)
    kinds = [kind for kind, _ in server.messages]
    assert kinds.index('StopDeviceCmd') < kinds.index('ScalarCmd')
    assert server.of_kind('ScalarCmd')[0]['Scalars'][0]['Scalar'] == .25
    assert ctl.devices[key].values[0] == 25
    assert server.of_kind('StartScanning')


async def test_hot_added_device_creates_controls_without_reload(native_server, controllers):
    server = await native_server()
    ctl = await controllers(server)
    await eventually(lambda: ctl.connected)
    await server.emit('DeviceAdded', device(index=3, name='Second device'))
    await eventually(lambda: ctl.ready_count == 1)
    assert next(iter(ctl.devices.values())).description.name == 'Second device'


async def test_slow_device_does_not_block_second(native_server, controllers):
    server = await native_server([device(0), device(1, 'Nova')])
    async def slow(ws, body):
        if body['DeviceIndex'] == 0:
            await asyncio.sleep(0.2)
        return False
    server.hooks['StopDeviceCmd'] = slow
    ctl = await controllers(server)
    await eventually(lambda: any(d.description.index == 1 and d.ready for d in ctl.devices.values()))
    assert not next(d for d in ctl.devices.values() if d.description.index == 0).ready
    await eventually(lambda: ctl.ready_count == 2)


async def test_failed_initial_stop_blocks_only_that_device(native_server, controllers):
    server = await native_server([device(0), device(1, 'Nova')])
    async def reject(ws, body):
        if body['DeviceIndex'] == 0:
            await ws.send_json([{'Error': {'Id': body['Id'], 'ErrorMessage': 'stop failed', 'ErrorCode': 4}}])
            return True
        return False
    server.hooks['StopDeviceCmd'] = reject
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    failed = next(d for d in ctl.devices.values() if d.description.index == 0)
    with pytest.raises(ProtocolError):
        await ctl.set_vibration(failed.key, 0, 10)
    assert not server.of_kind('ScalarCmd')


async def test_stop_fences_throttled_output(native_server, controllers):
    server = await native_server([device(gap=150)])
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    d = next(iter(ctl.devices.values())); d.last_output_at = time.monotonic()
    pending = asyncio.create_task(ctl.set_vibration(d.key, 0, 30))
    await asyncio.sleep(.01)
    await ctl.stop_device(d.key)
    with pytest.raises(DisconnectedError, match='cancelled'):
        await pending
    assert not server.of_kind('ScalarCmd')


async def test_remove_readd_index_cannot_receive_queued_output(native_server, controllers):
    server = await native_server([device(gap=150)])
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    d = next(iter(ctl.devices.values())); old_instance = d.instance
    d.last_output_at = time.monotonic()
    pending = asyncio.create_task(ctl.set_vibration(d.key, 0, 40))
    await asyncio.sleep(.01)
    await server.emit('DeviceRemoved', {'DeviceIndex': 0})
    await server.emit('DeviceAdded', device(gap=150))
    await eventually(lambda: ctl.devices[d.key].instance != old_instance and ctl.devices[d.key].ready)
    with pytest.raises(DisconnectedError):
        await pending
    assert not server.of_kind('ScalarCmd')


async def test_server_reconnect_does_not_replay_nonzero(native_server, controllers):
    server = await native_server([device()])
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    d = next(iter(ctl.devices.values()))
    await ctl.set_vibration(d.key, 0, 45)
    await server.disconnect()
    await eventually(lambda: server.handshakes >= 2 and ctl.ready_count == 1)
    assert len(server.of_kind('ScalarCmd')) == 1
    assert ctl.devices[d.key].values[0] == 0


async def test_no_auto_reconnect_option(native_server, controllers):
    server = await native_server([device()])
    ctl = await controllers(server, auto_reconnect=False)
    await eventually(lambda: ctl.ready_count == 1)
    await server.disconnect()
    await eventually(lambda: ctl.status == 'disconnected')
    await asyncio.sleep(.1)
    assert server.handshakes == 1
    assert ctl.enabled  # The master switch is available, not an unloaded entry.


async def test_disable_stops_and_releases_server(native_server, controllers):
    server = await native_server([device()])
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    await ctl.set_enabled(False)
    assert server.of_kind('StopAllDevices')
    assert ctl.status == 'disabled' and not ctl.connected
    assert not ctl._jobs


async def test_manual_watchdog_stops_without_new_input(native_server, controllers):
    server = await native_server([device()])
    ctl = await controllers(server, control_timeout=1)
    await eventually(lambda: ctl.ready_count == 1)
    d = next(iter(ctl.devices.values()))
    count = len(server.of_kind('StopDeviceCmd'))
    await ctl.set_vibration(d.key, 0, 20)
    await eventually(lambda: len(server.of_kind('StopDeviceCmd')) > count, timeout=2)
    await eventually(lambda: d.values[0] == 0)


async def test_identical_devices_fail_closed(native_server, controllers):
    server = await native_server([device(0), device(1)])
    ctl = await controllers(server)
    await eventually(lambda: ctl.connected_device_count == 2)
    await asyncio.sleep(.03)
    assert ctl.ready_count == 0
    for d in ctl.devices.values():
        assert d.ambiguous
        with pytest.raises(ProtocolError):
            await ctl.set_vibration(d.key, 0, 10)


async def test_unique_display_names_allow_identical_models(native_server, controllers):
    server = await native_server([device(0, display='One'), device(1, display='Two')])
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 2)
    assert {d.description.label for d in ctl.devices.values()} == {'One', 'Two'}


async def test_battery_read_is_advertised_and_scaled(native_server, controllers):
    server = await native_server([device(battery=True)])
    ctl = await controllers(server)
    await eventually(lambda: any(d.battery == 72 for d in ctl.devices.values()))
    assert server.of_kind('SensorReadCmd')[0]['SensorType'] == 'Battery'


async def test_metadata_restore_does_not_restore_control(native_server, controllers, session):
    from intiha_core.controller import Controller
    server = await native_server([device()]); ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    d = next(iter(ctl.devices.values())); await ctl.set_vibration(d.key, 0, 50)
    saved = ctl.export_known_devices()
    second = Controller(session, server.url, 'test-entry')
    second.restore_known_devices(saved)
    restored = second.devices[d.key]
    assert not restored.ready and not restored.connected and restored.values[0] is None


async def test_invalid_motor_value_sends_nothing(native_server, controllers):
    server = await native_server([device()]); ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    key = next(iter(ctl.devices))
    with pytest.raises(ValueError): await ctl.set_vibration(key, 0, float('nan'))
    with pytest.raises(ValueError): await ctl.set_vibration(key, 9, 10)
    assert not server.of_kind('ScalarCmd')


async def test_failed_output_not_replayed_and_attempts_stop(native_server, controllers):
    server = await native_server([device()]); ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    async def reject(ws, body):
        await ws.send_json([{'Error': {'Id': body['Id'], 'ErrorMessage': 'GATT failed', 'ErrorCode': 4}}])
        return True
    server.hooks['ScalarCmd'] = reject
    key = next(iter(ctl.devices)); count = len(server.of_kind('StopDeviceCmd'))
    with pytest.raises(ServerError): await ctl.set_vibration(key, 0, 20)
    await eventually(lambda: len(server.of_kind('StopDeviceCmd')) > count)
    assert len(server.of_kind('ScalarCmd')) == 1


async def test_scanning_finished_before_ack_is_not_overwritten(native_server, controllers):
    server = await native_server()
    async def finish(ws, body):
        await ws.send_json([{'ScanningFinished': {'Id': 0}}])
        await asyncio.sleep(.01)
        return False
    server.hooks['StartScanning'] = finish
    ctl = await controllers(server)
    await eventually(lambda: server.of_kind('StartScanning'))
    await asyncio.sleep(.05)
    assert not ctl.scanning


async def test_stop_all_fences_waiting_output(native_server, controllers):
    server = await native_server([device(gap=150)])
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1)
    d = next(iter(ctl.devices.values())); d.last_output_at = time.monotonic()
    pending = asyncio.create_task(ctl.set_vibration(d.key, 0, 30))
    await asyncio.sleep(.01)
    await ctl.stop_all()
    with pytest.raises(DisconnectedError): await pending
    assert not server.of_kind('ScalarCmd')


async def test_server_offline_does_not_end_master_lifecycle(session):
    from intiha_core.controller import Controller
    ctl = Controller(session, 'ws://127.0.0.1:1', 'x', request_timeout=.05,
                     retry_initial=.01, retry_max=.02)
    await ctl.set_enabled(True)
    try:
        await eventually(lambda: ctl.reconnect_count >= 1)
        assert ctl.enabled
        assert ctl.last_error
    finally:
        await ctl.set_enabled(False)
    assert ctl.status == 'disabled'


async def test_slow_scan_does_not_block_output_timeout(native_server, controllers):
    server = await native_server([device()])
    async def slow_scan(ws, body):
        await asyncio.sleep(1.8)
        return False
    server.hooks["StartScanning"] = slow_scan
    ctl = await controllers(server, control_timeout=1, request_timeout=2.5)
    await eventually(lambda: ctl.ready_count == 1)
    key = next(iter(ctl.devices))
    await ctl.set_vibration(key, 0, 10)
    await eventually(lambda: len(server.of_kind("StopDeviceCmd")) >= 2, timeout=1.6)
    assert ctl.devices[key].values[0] == 0


async def test_native_scan_error_does_not_unload_ready_device(native_server, controllers):
    server = await native_server([device()])
    async def scan_error(ws, body):
        await ws.send_json([{"Error": {"Id": body["Id"], "ErrorMessage": "adapter unavailable", "ErrorCode": 4}}])
        return True
    server.hooks["StartScanning"] = scan_error
    ctl = await controllers(server)
    await eventually(lambda: ctl.ready_count == 1 and ctl.last_error is not None)
    assert ctl.connected
    assert "discovery failed" in ctl.last_error
