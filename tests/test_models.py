import pytest
from intiha_core.models import Description, ProtocolError, normalise_url, percent
from conftest import device


@pytest.mark.parametrize("raw, expected", [
    ("192.168.0.12", "ws://192.168.0.12:12345"),
    ("host.local:12347", "ws://host.local:12347"),
    ("ws://host.local:12347/", "ws://host.local:12347"),
    ("wss://host.local/intiface", "wss://host.local/intiface"),
    ("::1", "ws://[::1]:12345"),
    ("[::1]:12347", "ws://[::1]:12347"),
])
def test_url(raw, expected):
    assert normalise_url(raw) == expected


@pytest.mark.parametrize("value", ["", "  ", "http://localhost", "ws://", "ws://h:0", "ws://h:65536",
    "ws://u:p@h", "ws://h/?token=secret", "ws://h/#abc", "ws://h/bridge", "ws://h/bridge/",
    "ws://host name:2", "ws://h:abcd", None])
def test_bad_url(value):
    with pytest.raises(ValueError):
        normalise_url(value)


@pytest.mark.parametrize("value", [-1, 101, float('nan'), float('inf'), -float('inf'), True, "10", None])
def test_bad_percent(value):
    with pytest.raises(ValueError):
        percent(value)


@pytest.mark.parametrize("value", [0, 1, 50, 99.5, 100])
def test_percent(value):
    assert percent(value) == value


def test_descriptor_preserves_scalar_indices():
    raw = device(motors=2)
    raw['DeviceMessages']['ScalarCmd'].insert(0, {'ActuatorType': 'Rotate', 'StepCount': 10})
    desc = Description.parse(raw)
    assert [m.index for m in desc.motors] == [1, 2]


def test_identity_not_device_index():
    assert Description.parse(device(index=0)).identity('s') == Description.parse(device(index=77)).identity('s')


def test_display_names_disambiguate():
    assert Description.parse(device(display='A')).identity('s') != Description.parse(device(display='B')).identity('s')


@pytest.mark.parametrize("field, value", [('DeviceIndex', True), ('DeviceIndex', -1),
    ('DeviceName', ''), ('DeviceMessages', []), ('DeviceMessageTimingGap', -1)])
def test_invalid_device(field, value):
    raw = device(); raw[field] = value
    with pytest.raises(ProtocolError):
        Description.parse(raw)
