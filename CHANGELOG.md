# Changelog

## 0.2.0

Plug-and-play setup and automatic compatible-device connection.

- Remove IntiHA bridge/client tokens; ordinary host/port clients connect directly.
- Discover the HA engine app through Supervisor with one empty confirmation.
  Manual fallback accepts a host/IP or full bridge URL.
- Automatically discover and connect supported BLE devices while enabled,
  with no approval list, MAC address entry or required Search step.
- Start enabled on a new installation and preserve the saved master choice.
- Add all device sensors, stop controls and supported sliders dynamically,
  retaining stable identities and user-assigned names.
- Remove old credentials/selection during configuration migration; keep
  existing useful options and remembered devices.
- Preserve cached stops, exclusive controller ownership, connection-generation
  checks and stopped reconnection. Reset retry backoff after successful readiness.
- Fix discovery during battery polling and commands waiting across a bridge
  reconnect, with regression tests.
- Simplify app/Compose setup and validate startup/discovery with a simulated
  Supervisor, plus automatic device arrival through the real engine/HA stack.

## 0.1.0

Initial implementation for physical hardware evaluation.

- HACS integration with offline-capable setup, options, explicit device
  approval, master switch, search, reconnect, sensors and vibration controls.
- HA Engine app using the pinned official Rust Buttplug server and protocol
  profiles, supporting authenticated clients and active HA Bluetooth paths.
- Bounded requests, separate client/bridge credentials, single-controller
  ownership, generation isolation, stop-plan fallback and no command replay.
- Automated HA, BLE-transport, Rust and cross-process tests; CI metadata and
  app-image checks; physical acceptance procedure.
