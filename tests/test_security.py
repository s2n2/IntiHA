"""ATT security classification and the deliberately narrow Bond policy."""

from types import SimpleNamespace

import pytest

from custom_components.intiha.security import is_gatt_security_error, profile_requests_pairing


@pytest.mark.parametrize(
    "message",
    [
        "Bluetooth GATT Error address=AA handle=27 error=15 description=Insufficient encryption",
        "ATT Error: Insufficient Authentication",
        "ESP_GATT_INSUF_ENCRYPTION",
        "ESP_GATT_INSUF_AUTHENTICATION",
    ],
)
def test_explicit_att_security_rejection(message):
    assert is_gatt_security_error(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    [
        "ESP_GATT_CONN_TERMINATE_PEER_USER",
        "Error ESP_GATT_ERROR while connecting",
        "Authenticated connection not ready yet; ConnectionState.HOST_RESOLVING",
        "Noise encryption key is invalid",
        "BLE connection timed out after 40s",
        "Bluetooth error=15",
        "Pairing failed due to error: 82",
        "Insufficient resources",
    ],
)
def test_not_a_proven_att_security_rejection(message):
    assert not is_gatt_security_error(RuntimeError(message))


def test_wrapped_error_retains_original_security_cause():
    original = RuntimeError("Insufficient encryption")
    outer = RuntimeError("Wrapped Bluetooth failure")
    outer.__cause__ = original
    assert is_gatt_security_error(outer)


def test_cyclic_exception_context_terminates():
    first = RuntimeError("other")
    second = RuntimeError("other")
    first.__context__ = second
    second.__context__ = first
    assert not is_gatt_security_error(first)


@pytest.mark.parametrize(
    "name,protocols,expected",
    [
        ("Bond", ["wevibe-8bit"], True),
        (" bond ", ["wevibe-8bit"], True),
        ("Bond", ["wevibe"], False),
        ("Bond", [], False),
        ("Bond", None, False),
        ("Bond", "wevibe-8bit", False),
        ("Bond", {"wevibe-8bit": True}, False),
        ("Nova", ["wevibe"], False),
        ("Other device", ["wevibe-8bit"], False),
    ],
)
def test_pairing_policy_is_not_a_generic_name_or_brand_match(name, protocols, expected):
    assert profile_requests_pairing(name, protocols) is expected


@pytest.mark.parametrize("code", [5, 15])
def test_typed_att_error_code_is_recognised(code):
    class BluetoothGATTAPIError(Exception):
        def __init__(self):
            self.error = SimpleNamespace(error=code)
            super().__init__("GATT rejected")

    assert is_gatt_security_error(BluetoothGATTAPIError())


def test_other_error_namespace_with_same_numeric_code_is_not_att_security():
    class BluetoothConnectionError(Exception):
        def __init__(self):
            self.error = SimpleNamespace(error=15)
            super().__init__("Connection rejected")

    assert not is_gatt_security_error(BluetoothConnectionError())
