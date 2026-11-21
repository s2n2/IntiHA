"""Protect the standard public Buttplug WebSocket from custom extensions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_public_websocket_forwards_messages_without_custom_fields():
    source = (ROOT / "engine" / "src" / "server.rs").read_text()
    assert "decorate_public_message" not in source
    assert "BluetoothAddress" not in source
    # Display names are only offered on the /intiha/devices side channel,
    # never added to Buttplug messages themselves.
    start = source.index("pub async fn device_identities")
    end = source.index("\n    pub ", start + 1)
    assert "DeviceDisplayName" not in source[:start] + source[end:]
    assert "Message::Text(text.into())" in source
    assert "Message::Text(app.decorate_public_message(" not in source

def test_bridge_does_not_require_external_name_metadata():
    bridge = (ROOT / "engine" / "src" / "bridge.rs").read_text()
    coordinator = (ROOT / "custom_components" / "intiha" / "coordinator.py").read_text()
    # The engine learns names itself (advertisements and GATT 0x2A00); it
    # needs no name list from Home Assistant.
    assert "pub fn learn_name" in bridge
    assert '"known_names":' not in coordinator
