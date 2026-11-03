"""IntiHA native-engine reference implementation."""
DOMAIN = "intiha"
VERSION = "0.3.0b1"
PLATFORMS = ["sensor", "binary_sensor", "switch", "button", "number"]
CONF_ENGINE_URL = "engine_url"
CONF_AUTO_RECONNECT = "auto_reconnect"
CONF_AUTO_SCAN = "auto_scan"
CONF_CONTROL_TIMEOUT = "control_timeout"
CONF_MIGRATION_REQUIRED = "migration_required"
DEFAULT_ENGINE_URL = "ws://127.0.0.1:12347"
DEFAULT_CONTROL_TIMEOUT = 60
