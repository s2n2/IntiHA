"""Token-free offline setup, Supervisor discovery, and simple optional settings."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiha.config_flow import normalise_engine_url, validate_connection
from custom_components.intiha.const import DOMAIN

DATA = {"engine_url": "ws://engine.example:12346/bridge"}
DISCOVERY = HassioServiceInfo(
    config={"host": "test-intiha-engine", "port": 12346},
    name="IntiHA Engine",
    slug="test_intiha_engine",
    uuid="8dd6938678564e9d87448f703b9bbdd1",
)


@pytest.mark.parametrize(
    "url",
    [
        "http://engine/bridge",
        "ws://engine/unrelated",
        "ws://engine/bridge?extra=value",
        "ws://user:password@engine/bridge",
        "ws://engine:70000/bridge",
        "ws:///bridge",
        "ws://engine/bridge#fragment",
        "bad host",
        "",
    ],
)
def test_invalid_engine_url(url):
    assert validate_connection({"engine_url": url}) == {"engine_url": "invalid_url"}


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("192.168.0.10", "ws://192.168.0.10:12346/bridge"),
        ("engine.local:23456", "ws://engine.local:23456/bridge"),
        ("::1", "ws://[::1]:12346/bridge"),
        ("ws://engine:12346/", "ws://engine:12346/bridge"),
        ("wss://engine.example/bridge", "wss://engine.example/bridge"),
    ],
)
def test_address_can_be_a_simple_host(address, expected):
    assert validate_connection({"engine_url": address}) == {}
    assert normalise_engine_url(address) == expected


async def _setup_ok(*args):
    return True


async def test_setup_does_not_probe_offline_engine(hass, monkeypatch):
    monkeypatch.setattr("custom_components.intiha.async_setup_entry", _setup_ok)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert set(result["data_schema"].schema) == {"engine_url"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA
    assert result["title"] == "IntiHA"
    assert result["result"].version == 2
    await hass.async_block_till_done()


async def test_duplicate_setup_aborts(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN, version=2)
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_connection_validation_can_be_corrected(hass, monkeypatch):
    monkeypatch.setattr("custom_components.intiha.async_setup_entry", _setup_ok)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"engine_url": "http://invalid/"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"engine_url": "invalid_url"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"engine_url": "engine.example"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA
    await hass.async_block_till_done()


async def test_supervisor_discovery_needs_only_confirmation(hass, monkeypatch):
    monkeypatch.setattr("custom_components.intiha.async_setup_entry", _setup_ok)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    assert not result["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"engine_url": "ws://test-intiha-engine:12346/bridge"}
    assert result["result"].unique_id == DOMAIN
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "config",
    [{}, {"host": "engine", "port": 0}, {"host": "bad host"}, {"host": "engine", "port": True}],
)
async def test_invalid_discovery_is_rejected(hass, config):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_HASSIO},
        data=HassioServiceInfo(
            config=config, name="IntiHA Engine", slug=DISCOVERY.slug, uuid=DISCOVERY.uuid
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"


async def test_discovery_does_not_duplicate_existing_setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN, version=2)
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == DATA


async def test_manual_add_finds_existing_app_announcement(hass, monkeypatch):
    monkeypatch.setattr("custom_components.intiha.async_setup_entry", _setup_ok)
    monkeypatch.setattr("custom_components.intiha.config_flow.is_hassio", lambda hass: True)
    client = SimpleNamespace(
        discovery=SimpleNamespace(
            list=AsyncMock(return_value=[SimpleNamespace(service=DOMAIN, config=DISCOVERY.config)])
        )
    )
    monkeypatch.setattr(
        "custom_components.intiha.config_flow.get_supervisor_client", lambda hass: client
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "hassio_confirm"
    assert not result["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"engine_url": "ws://test-intiha-engine:12346/bridge"}
    await hass.async_block_till_done()


async def test_supervisor_unavailable_keeps_manual_setup_available(hass, monkeypatch):
    monkeypatch.setattr("custom_components.intiha.config_flow.is_hassio", lambda hass: True)
    client = SimpleNamespace(discovery=SimpleNamespace(list=AsyncMock(side_effect=TimeoutError)))
    monkeypatch.setattr(
        "custom_components.intiha.config_flow.get_supervisor_client", lambda hass: client
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    assert set(result["data_schema"].schema) == {"engine_url"}


async def test_options_have_no_device_approval_step(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="IntiHA", data=DATA, unique_id=DOMAIN, version=2)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"connection", "behaviour"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "connection"}
    )
    assert set(result["data_schema"].schema) == {"engine_url"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"engine_url": "different-engine"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"engine_url": "ws://different-engine:12346/bridge"}


async def test_behaviour_limits(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN, version=2)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "behaviour"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"auto_reconnect": False, "control_timeout": 45, "scan_duration": 15},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["auto_reconnect"] is False
    assert result["data"]["control_timeout"] == 45
