"""Optional classic We-Vibe firmware feedback, independent of Buttplug transport."""

from custom_components.intiha.ble import decode_wevibe_battery_frame, decode_wevibe_info
from custom_components.intiha.sensor import IntiHASensor

ADDRESS = 'AA:BB:CC:DD:EE:01'


def test_wevibe_info_decoder_validates_format_and_off_state():
    assert decode_wevibe_info(b'') is None
    assert decode_wevibe_info(bytes([2, 3, 0, 0, 0, 0, 0xF9, 2])) is None
    reading = decode_wevibe_info(bytes([1, 3, 0, 0, 0, 0, 0xF9, 2]))
    assert reading == {
        'intensity': 100, 'internal_intensity': 100,
        'external_intensity': 60, 'pattern': 'vibrate',
        'pattern_code': 3, 'battery': 0,
    }
    # Pattern=off is authoritative even if intensity bytes linger.
    stopped = decode_wevibe_info(bytes([1, 0, 0, 0, 0, 0, 0xFF, 2]))
    assert stopped['intensity'] == 0
    assert stopped['internal_intensity'] == 100
    assert decode_wevibe_info(bytes([1, 7, 0, 0, 0, 0, 0xF0, 2]))['pattern'] == 'wave'


def test_current_firmware_header_0x04_needs_a_known_model_byte():
    # Real Ditto frame: header 0x04, pattern off, battery cb d8, model 0x40.
    ditto = decode_wevibe_info(bytes.fromhex('0400d8cb8e140040'))
    assert ditto['pattern'] == 'off' and ditto['intensity'] == 0
    assert ditto['battery'] == 80
    assert decode_wevibe_info(bytes.fromhex('0400d8cb8e1400ff')) is None
    # Chorus-family (Sync 2) 20-byte frames are a different format.
    assert decode_wevibe_info(bytes.fromhex('040000f7251c1cf5021e000300001e0000140005')) is None


def test_sync2_twenty_byte_frame_reports_battery_only():
    assert decode_wevibe_battery_frame(bytes.fromhex('040000f7251c1cf5021e000300001e0000140005')) == 97
    assert decode_wevibe_battery_frame(bytes.fromhex('040000f6251d1df5021e000300001e0000140005')) == 96
    assert decode_wevibe_battery_frame(bytes.fromhex('010300f5251e00f5021e000100001e0000140005')) == 96
    assert decode_wevibe_battery_frame(bytes.fromhex('0400d8cb8e140040')) is None


async def test_reporting_uses_only_current_session_and_disappears_on_disconnect(coordinator):
    coordinator.engine_connected = True
    coordinator._update_devices([{
        'address': ADDRESS, 'connection_id': 'session-1',
        'ready': True, 'DeviceIndex': 1,
        'DeviceName': 'We-Vibe Jive', 'DeviceMessages': {},
    }])
    sensor = IntiHASensor(coordinator, 'reported_intensity', ADDRESS)
    assert not sensor.available
    await coordinator._async_ble_event({
        'type': 'device_report', 'address': ADDRESS,
        'connection_id': 'old-session', 'intensity': 89,
        'internal_intensity': 89, 'external_intensity': 0,
        'pattern': 'vibrate', 'pattern_code': 3,
    })
    assert not sensor.available
    await coordinator._async_ble_event({
        'type': 'device_report', 'address': ADDRESS,
        'connection_id': 'session-1', 'intensity': 60,
        'internal_intensity': 60, 'external_intensity': 0,
        'pattern': 'wave', 'pattern_code': 7,
    })
    assert sensor.available and sensor.native_value == 60
    assert sensor.extra_state_attributes['value_type'] == 'device_reported_setting'
    assert sensor.extra_state_attributes['bluetooth_address'] == ADDRESS
    assert sensor.extra_state_attributes['physical_motor_measurement'] is False
    await coordinator._async_ble_event({
        'type': 'device_report_unavailable', 'address': ADDRESS,
        'connection_id': 'old-session',
    })
    assert sensor.available
    await coordinator._async_ble_event({
        'type': 'device_report_unavailable', 'address': ADDRESS,
        'connection_id': 'session-1',
    })
    assert not sensor.available
    coordinator._update_devices([])
    assert not sensor.available
