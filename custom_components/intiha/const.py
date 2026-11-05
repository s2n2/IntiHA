"""Constants for IntiHA."""

from homeassistant.const import Platform

DOMAIN = "intiha"
VERSION = "0.3.0b6"
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]
CONF_ENGINE_URL = "engine_url"
CONF_AUTO_RECONNECT = "auto_reconnect"
CONF_CONTROL_TIMEOUT = "control_timeout"
CONF_SCAN_DURATION = "scan_duration"
DEFAULT_ENGINE_URL = "ws://homeassistant.local:12346/bridge"
DEFAULT_CONTROL_TIMEOUT = 60
DEFAULT_SCAN_DURATION = 30
MAX_MESSAGE_BYTES = 262144
MAX_PENDING_REQUESTS = 32
MAX_BLE_REQUESTS = 16
MAX_KNOWN_DEVICES = 256
ADMIN_TIMEOUT = 45
