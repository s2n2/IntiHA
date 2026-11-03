# IntiHA Native Engine: setup and limits

This is a new app (`intiha_native`), separate from the old `intiha_engine` app.
Stop the old app before testing; do not allow competing device controllers.

## Requirements

- Home Assistant with Supervisor, amd64 or aarch64.
- A native Bluetooth controller exposed through the host's BlueZ/D-Bus service.
  In a VM, configure controller passthrough on the host as appropriate.
- The HACS IntiHA 0.3.0b1 integration for HA controls.

A garage ESP32 D1 Mini running ESPHome is NOT a native host adapter. Without a
local controller, use normal Intiface Central on your existing phone/computer
and point the HA integration at that server. The app is optional in that setup.

## Start

Install with default options and start the app. It builds exact upstream
`intiface-engine =4.1.0` with `cargo install --locked`. This is a pinned reference
version, not a latest-release claim. The CLI and device protocols are unmodified.

Listen ports: 12347 = standard Buttplug WebSocket; 12348 = simple process-health
HTTP endpoint. Connect HA to `ws://HA_HOST_IP:12347`, or accept discovery if the
Supervisor announcement appears. Existing migrated entries use Configure.

Only `log_level` is an app option. Keep `info` unless gathering a controlled log;
Bluetooth/device identifiers can be visible in debug logs. No user token is
required. Supervisor supplies its own internal discovery credential automatically.
The native engine subprocess does not receive that credential.

## Permissions

`host_dbus` maps the host's system bus. `host_network` makes the native WebSocket
reachable and permits the advertised HA-host address. The app does not request
full privileged mode, raw USB ownership, Docker access or blanket host paths.
It does not start a second bluetoothd. Actual HA OS D-Bus/AppArmor behaviour has
not been verified in the authoring environment and is a release test requirement.

## Safety and availability

The upstream server heartbeat is configured to 10 seconds. HA separately uses
a configurable output-command timer. Neither is physical stop feedback.
The `/health` endpoint reports process liveness only; it never connects as a
second Buttplug client and never claims Bluetooth is healthy. A process can be
alive while a Bluetooth adapter or peripheral is unavailable.

No authentication is added to the client WebSocket by request. Keep ports on a
trusted LAN and never port-forward them to the internet. Only one controlling
application should own the native server at a time.

## Build and runtime diagnostics

The Dockerfile gets dependency licences from Cargo's actual source cache. It
has no `COPY licenses` assumption. CI builds both architectures and runs the
actual binary's protocol smoke test without hardware. Physical radio behaviour
requires a separate test on your host.

Missing `/run/dbus/system_bus_socket` produces an explicit startup error. A
busy port produces a startup error rather than silently replacing another app.
Check the engine log for the actual upstream version and adapter errors. The
engine's version (4.1.0) differs from this packaging release (0.3.0b1).
