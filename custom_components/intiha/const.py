"""Constants for IntiHA."""

from homeassistant.const import Platform

DOMAIN = "intiha"
VERSION = "2.4.0"
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
CONF_IDLE_OFF = "idle_off_minutes"
DEFAULT_ENGINE_URL = "ws://homeassistant.local:12346/bridge"
DEFAULT_CONTROL_TIMEOUT = 60
DEFAULT_SCAN_DURATION = 30
# Minutes with nothing playing before IntiHA switches itself off (0 = never):
# connected toys are kept awake (keep-alive, battery reads), so they drain.
DEFAULT_IDLE_OFF = 15
MAX_MESSAGE_BYTES = 262144
MAX_PENDING_REQUESTS = 32
MAX_BLE_REQUESTS = 16
MAX_KNOWN_DEVICES = 256
ADMIN_TIMEOUT = 45
