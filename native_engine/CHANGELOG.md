# Changelog

## 0.3.0b1

New experimental app, different from the retired custom IntiHA Engine.
- Unmodified pinned upstream Intiface Engine 4.1.0.
- Native host Bluetooth through D-Bus; no ESPHome proxy motor-control backend.
- Port 12347, protocol heartbeat, process-only health endpoint and Supervisor discovery.
- Tokenless trusted-LAN client endpoint; no user-entered secrets.
- Locked Cargo installation and build-time dependency licence collection.
- Container build, actual HA OS installation and physical device qualification
  remain required outside the authoring environment.
