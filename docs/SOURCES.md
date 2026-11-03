# Implementation references

Checked during this build, 3 November 2026. Source observations guide the code;
physical and installed-runtime behaviour still require the qualification plan.

- Upstream engine package/version history: https://docs.rs/crate/intiface-engine
- Engine changelog (4.1.0 and later CLI breaking changes): https://docs.rs/crate/intiface-engine/5.0.2/source/CHANGELOG.md
- Maintained upstream repository: https://github.com/buttplugio/buttplug
- Standard protocol and historical v3 message definitions: https://buttplug.io/docs/spec/deprecated/
- Discovery messages: https://buttplug.io/docs/spec/device_discovery/
- Server/client heartbeat: https://buttplug.io/docs/dev-guide/cookbook/connections/ping/
- HA Bluetooth authoring: https://developers.home-assistant.io/docs/bluetooth/
- HA app packaging/host_dbus: https://developers.home-assistant.io/docs/apps/configuration/
- ESPHome BLE backend: https://raw.githubusercontent.com/Bluetooth-Devices/bleak-esphome/main/src/bleak_esphome/backend/client.py
- HA-selected Bluetooth wrapper: https://github.com/Bluetooth-Devices/habluetooth
- Reported SDK security regressions (not established for the garage board): https://github.com/espressif/esp-idf/issues/19286 and https://github.com/espressif/esp-idf/issues/19303

The protocol v3 ServerInfo message should return the version requested by the
client. Indices are session-scoped. The app pins engine 4.1.0 rather than relying
on latest engine CLI options; newer major engine releases change those options.
The CI smoke test runs the actual pinned binary with no hardware managers to
check this client's handshake and core protocol messages. It is not a BLE test.
