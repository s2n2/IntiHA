"""Configuration upgrades retain useful settings and remove obsolete setup."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiha import async_migrate_entry
from custom_components.intiha.const import DOMAIN


async def test_upgrade_removes_credentials_and_device_approval(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "engine_url": "ws://engine:12346/bridge",
            "bridge_token": "old-credential",
            "selected_devices": ["AA:BB:CC:DD:EE:FF"],
        },
        options={
            "engine_url": "ws://replacement:12346/bridge",
            "bridge_token": "old-option-credential",
            "selected_devices": ["AA:BB:CC:DD:EE:00"],
            "auto_reconnect": False,
            "control_timeout": 90,
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert entry.data == {"engine_url": "ws://engine:12346/bridge"}
    assert entry.options == {
        "engine_url": "ws://replacement:12346/bridge",
        "auto_reconnect": False,
        "control_timeout": 90,
    }


async def test_future_configuration_is_not_silently_downgraded(hass):
    entry = MockConfigEntry(domain=DOMAIN, version=3, data={"future_setting": True})
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)
    assert entry.version == 3
    assert entry.data == {"future_setting": True}
