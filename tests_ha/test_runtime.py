"""HA loading/configuration tests. Not executed in the authoring container."""
from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark=pytest.mark.ha


async def test_offline_setup_keeps_master_and_diagnostics(hass):
    entry=MockConfigEntry(domain='intiha',version=3,unique_id='intiha',
                          data={'engine_url':'ws://127.0.0.1:9'})
    entry.add_to_hass(hass)
    # We exercise the real HA loader, storage and entity platforms. Network
    # behaviour is covered separately; prevent a persistent retry task here.
    with patch('custom_components.intiha.coordinator.IntiHACoordinator.start',new=AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    switches=hass.states.async_all('switch')
    assert len(switches)==1 and switches[0].state==STATE_OFF
    assert all(s.state!='unavailable' for s in hass.states.async_all('sensor'))
    assert hass.services.has_service('intiha','probe_proxy')
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_old_entry_migrates_disabled_without_legacy_bridge(hass):
    entry=MockConfigEntry(domain='intiha',version=2,unique_id='intiha',
                          data={'engine_url':'ws://old:12346/bridge'})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version==3
    assert entry.data['migration_required'] is True
    assert entry.runtime_data.controller.enabled is False
    assert entry.runtime_data.controller.status=='migration_required'
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_user_flow_rejects_bridge_accepts_offline_native_url(hass):
    result=await hass.config_entries.flow.async_init('intiha',context={'source':'user'})
    result=await hass.config_entries.flow.async_configure(result['flow_id'],
                        {'engine_url':'ws://old:12346/bridge'})
    assert result['errors']=={'engine_url':'invalid_url'}
    with patch('custom_components.intiha.coordinator.IntiHACoordinator.start',new=AsyncMock()):
        result=await hass.config_entries.flow.async_configure(result['flow_id'],
                            {'engine_url':'ws://127.0.0.1:9'})
        await hass.async_block_till_done()
    assert result['type']=='create_entry'
    assert result['data']['engine_url']=='ws://127.0.0.1:9'
    await hass.config_entries.async_unload(result['result'].entry_id)
