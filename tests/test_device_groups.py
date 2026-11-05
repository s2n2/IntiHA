"""Pure device grouping policy regression checks."""
from types import SimpleNamespace
from custom_components.intiha.device_groups import device_group


def test_exclusive_groups():
    assert device_group(SimpleNamespace(connect_enabled=True, connected=True)) == "connected"
    assert device_group(SimpleNamespace(connect_enabled=True, connected=False)) == "known"
    assert device_group(SimpleNamespace(connect_enabled=False, connected=True)) == "disabled"
    assert device_group(SimpleNamespace(connect_enabled=False, connected=False)) == "disabled"
