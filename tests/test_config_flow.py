"""Setup works offline and credentials/device approval are validated."""

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiha.config_flow import validate_connection
from custom_components.intiha.const import DOMAIN

DATA = {"engine_url": "ws://engine.example:12346/bridge", "bridge_token": "a" * 64}


@pytest.mark.parametrize(
    "url",
    [
        "http://engine/bridge",
        "ws://engine/",
        "ws://engine/bridge?token=hidden",
        "ws://user:password@engine/bridge",
        "ws://engine:70000/bridge",
        "ws:///bridge",
        "ws://engine/bridge#fragment",
    ],
)
def test_invalid_engine_url(url):
    assert validate_connection({**DATA, "engine_url": url}) == {"engine_url": "invalid_url"}


@pytest.mark.parametrize(
    "token", ["short", " " + "x" * 32, "x" * 32 + "\n", "\u2603" * 32, "a b" * 16]
)
def test_invalid_secret(token):
    assert validate_connection({**DATA, "bridge_token": token}) == {"bridge_token": "invalid_token"}


async def test_setup_does_not_probe_offline_engine(hass, monkeypatch):
    monkeypatch.setattr("custom_components.intiha.async_setup_entry", lambda *args: _setup_ok())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA
    assert result["title"] == "IntiHA"
    await hass.async_block_till_done()


async def _setup_ok():
    return True


async def test_duplicate_setup_aborts(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_connection_validation_can_be_corrected(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "bridge_token": "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"bridge_token": "invalid_token"}


async def test_options_explicitly_approve_and_normalise_addresses(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="IntiHA", data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "selected_devices": [],
            "manual_addresses": "aa-bb-cc-dd-ee-01, AA:BB:CC:DD:EE:01",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["selected_devices"] == ["AA:BB:CC:DD:EE:01"]
    assert result["data"]["bridge_token"] == DATA["bridge_token"]


async def test_options_invalid_device_does_not_save(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"manual_addresses": "not-a-mac"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"manual_addresses": "invalid_address"}


async def test_behaviour_limits(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "behaviour"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "auto_reconnect": False,
            "control_timeout": 45,
            "scan_duration": 15,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["auto_reconnect"] is False
    assert result["data"]["control_timeout"] == 45
