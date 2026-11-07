"""Protect the standard public Buttplug WebSocket from custom extensions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_public_websocket_forwards_messages_without_custom_fields():
    source = (ROOT / "engine" / "src" / "server.rs").read_text()
    assert "decorate_public_message" not in source
    assert "BluetoothAddress" not in source
    assert "DeviceDisplayName" not in source
    assert "Message::Text(text.into())" in source
    assert "Message::Text(app.decorate_public_message(" not in source

def test_bridge_does_not_require_external_name_metadata():
    bridge = (ROOT / "engine" / "src" / "bridge.rs").read_text()
    coordinator = (ROOT / "custom_components" / "intiha" / "coordinator.py").read_text()
    assert "known_names" not in bridge
    assert '"known_names":' not in coordinator
