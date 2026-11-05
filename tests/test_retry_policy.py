"""Hardware-independent regression checks for BLE recovery classification."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "custom_components" / "intiha" / "retry_policy.py"
spec = spec_from_file_location("intiha_retry_policy_test", source)
module = module_from_spec(spec)
spec.loader.exec_module(module)


def test_no_backend_waits_for_proxy():
    assert module.classify_ble_failure(
        "No backend with an available connection slot that can reach address AA:BB was found"
    ) == ("waiting_for_proxy", 60.0)


def test_timeout_cools_down():
    assert module.classify_ble_failure(
        "Timeout waiting for connect response while connecting after 20.0s"
    ) == ("connection_timed_out", 45.0)


def test_transient_generic_error():
    assert module.classify_ble_failure("Device busy") == ("retrying", 15.0)


def test_recovery_not_mislabelled():
    assert module.classify_ble_failure("Waiting for Bluetooth recovery (40s remaining)")[0] == "waiting_for_proxy"
