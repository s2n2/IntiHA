"""Optional classic We-Vibe firmware feedback, independent of Buttplug transport."""

from custom_components.intiha.ble import (
    decode_wevibe_battery_frame,
    decode_wevibe_info,
    decode_wevibe_report,
    decode_wevibe_status_frame,
)
from custom_components.intiha.sensor import IntiHASensor

ADDRESS = 'AA:BB:CC:DD:EE:01'


def test_wevibe_info_decoder_validates_format_and_off_state():
    assert decode_wevibe_info(b'') is None
    assert decode_wevibe_info(bytes([2, 3, 0, 0, 0, 0, 0xF9, 2])) is None
    reading = decode_wevibe_info(bytes([1, 3, 0, 0, 0, 0, 0xF9, 2]))
    assert reading == {
        'intensity': 100, 'internal_intensity': 100,
        'external_intensity': 82, 'pattern': 'vibrate',
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
    assert decode_wevibe_battery_frame(bytes.fromhex('040000f7251c1cf5021e000300001e0000140005')) == 96
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


def test_classic_jive_reports_on_its_own_0_to_11_scale():
    # Captured from a Jive while sending 15/15 (100%) and 11/15 (~70%).
    assert decode_wevibe_info(bytes.fromhex('010340f094070b02'))['intensity'] == 100
    assert decode_wevibe_info(bytes.fromhex('010370f094060802'))['intensity'] == 73
    assert decode_wevibe_info(bytes.fromhex('040040f094070002'))['intensity'] == 0


def test_sync2_status_frame_reports_both_motors_and_masks_off_motors():
    name = 'Sync 2'
    # Motor 1 at 30% (9/30), motor 2 switched off but remembering 30/30.
    running = decode_wevibe_status_frame(bytes.fromhex('010300e925091ef5021e000100001e0000140005'), name)
    assert running['internal_intensity'] == 30 and running['external_intensity'] == 0
    assert running['intensity'] == 30 and running['battery'] == 91
    both = decode_wevibe_status_frame(bytes.fromhex('010300e725151bf5021e000300001e0000140005'), name)
    assert (both['internal_intensity'], both['external_intensity']) == (70, 90)
    idle = decode_wevibe_status_frame(bytes.fromhex('040000e725151bf5021e000300001e0000140005'), name)
    assert idle['intensity'] == 0 and idle['pattern'] == 'off'
    # Without a name, the non-empty tail still identifies the Chorus family.
    assert decode_wevibe_status_frame(bytes.fromhex('010300e725151bf5021e000300001e0000140005'))['external_intensity'] == 90


def test_bond_status_frame_uses_eight_bit_steps():
    # Bond reports Buttplug step + 3 (0-27 steps): 0x0b = 30%, 0x16 = 70%.
    frame = '010300b50d{:02x}1ef4011e0001' + '00' * 8
    assert decode_wevibe_status_frame(bytes.fromhex(frame.format(0x0B)))['intensity'] == 30
    assert decode_wevibe_status_frame(bytes.fromhex(frame.format(0x16)), 'Bond')['intensity'] == 70
    assert decode_wevibe_status_frame(bytes.fromhex(frame.format(0x1E)))['intensity'] == 100
    # Single motor: the second level byte is ignored.
    assert decode_wevibe_status_frame(bytes.fromhex(frame.format(0x11)))['external_intensity'] == 0
    stopped = decode_wevibe_status_frame(bytes.fromhex('040000b50d111ef4011e0001' + '00' * 8))
    assert stopped['intensity'] == 0
    assert decode_wevibe_report(bytes.fromhex('0400d8cb8e140040'))['pattern'] == 'off'
