# IntiHA Engine

A local Buttplug server using Home Assistant's Bluetooth network. The engine
runs the official Buttplug device protocols; the **IntiHA HACS integration**
supplies Bluetooth connections, HA controls and status sensors. Install both.

Version 0.2.0 requires no IntiHA tokens or device approval. Compatible devices
are found and connected automatically while the master switch is on.

## Home Assistant OS: install and start

1. Add `https://github.com/s2n2/IntiHA` to **HACS → Custom repositories** as an
   **Integration**. Install IntiHA and restart Home Assistant.
2. Add the same URL to **Settings → Apps → App store → Repositories**. Install
   **IntiHA Engine** and press **Start**, keeping the defaults.
3. Accept the discovered **IntiHA** integration in **Settings → Devices &
   services**. There are no fields to fill in. Manual **Add integration →
   IntiHA** also checks for the running app.
4. Turn on a compatible Bluetooth device. Its HA status, stop button and
   supported controls appear automatically. No Search or reload is required.

A new installation starts enabled with automatic reconnect. The master state
is remembered, so an installation you turned off stays off after a restart.
Enable the app's Start on boot and Watchdog options if desired.

Requirements are HA **2026.10+**, an **amd64 or aarch64** host, and an active
ESPHome proxy or connectable local Bluetooth adapter. The first installation
builds locked Rust dependencies locally and may take several minutes.

If discovery is unavailable, the integration asks only for a host/IP address.
It also accepts a complete bridge URL, `ws://HOST:12346/bridge`. Setup can be
completed while the engine is offline.

The app obtains its hostname and announces discovery through Supervisor using
the platform credential supplied automatically by HA. Users never create,
copy or configure that credential. The credential is removed from the engine
process environment. Discovery retries in the background without delaying
the server. No privileged mode, USB passthrough or Bluetooth access is needed
by this app.

## Connect an application

```text
ws://HOME_ASSISTANT_IP:12345
```

For separate host/port fields, use the HA machine's address and **12345**.
No query string or authorization header is needed. Both IntiHA WebSocket
ports accept local-network connections without authentication.

The official serializer supports negotiated Buttplug protocol versions 0–4.
Only one external controller can own devices at a time. HA movement sliders
are unavailable while an external application is connected, while stop and
status controls remain available.

Some HTTPS browser applications block connections to a remote `ws://` server.
Use the application's supported local connector or a correctly configured
`wss://` endpoint in that case.

## App settings

The defaults are sufficient for normal use.

| Setting | Default / meaning |
|---|---|
| Log level | `info`; `debug` is available for diagnosis. |
| Port 12345 | Buttplug clients. |
| Port 12346 | HA integration bridge; automatic discovery uses the internal app address. |

Automatic reconnect, search duration and the HA manual-control timeout are
optional integration settings. **Search devices** refreshes discovery and
retries devices now; ordinary discovery remains continuous. An explicit
search can retry a lost device when automatic reconnect is disabled.

The HTTP `/health` endpoint checks that the engine is responding. It remains
healthy when the integration is off or disconnected; HA sensors report the
actual bridge/device state.

## Bluetooth coverage and reconnection

Active ESPHome proxies and supported local adapters provide GATT control.
Shelly proxies contribute advertisements but cannot relay the active control
connection. See [ESPHome](https://esphome.io/components/bluetooth_proxy/) and
[HA Shelly](https://www.home-assistant.io/integrations/shelly/#bluetooth-support).

Use overlapping active coverage and check available connection slots. A
working Bluetooth link remains on its current adapter. If it breaks, the
integration resolves a fresh connectable path through HA. Successful device
readiness resets failure backoff; reconnects do not restore old motor output.
There can be a pause while loss is detected, the device advertises again and
a new link is established.

The engine identifies the device and installs its actual driver stop writes
in HA before admitting control. HA can attempt those writes if the engine
fails. Stops clear queued commands and release the affected link; automatic
reconnect then returns the device stopped. A ready-device link loss ends the
external controller session, so that application must reconnect and issue
fresh commands.

A failed radio link cannot carry a stop command. Uncertain stops remain
visible. Actual firmware behaviour and room coverage need physical testing;
software tests do not establish uninterrupted handover.

For the classic We-Vibe protocol, the exact known initialization pulse is
replaced with a stop packet. Native vendor patterns are not added in this
release. HA exposes vibration controls; other upstream actuator commands are
available through compatible Buttplug applications.

## Home Assistant Container / Docker

Install the integration in HA. On the Docker host, run:

```sh
cd engine
docker compose up -d --build
```

No secrets or `.env` file are needed. Enter the Docker host's IP in the
integration; applications use `ws://DOCKER_HOST_IP:12345`. The standalone
container skips Supervisor discovery and uses the same HA Bluetooth bridge.

Advanced environment overrides:

| Variable | Default |
|---|---|
| `INTIHA_BIND` | `0.0.0.0` |
| `INTIHA_CLIENT_PORT` | `12345` |
| `INTIHA_BRIDGE_PORT` | `12346` |
| `INTIHA_LOG_LEVEL` | `info` |
| `RUST_LOG` | Derived from log level |

## Upgrading from 0.1.0

Update both parts to 0.2.0. HA removes old credentials and selected-device
settings automatically while retaining useful options and saved device
metadata. Remove any token query string from existing client URLs. An
explicitly saved master-off state is preserved.

## Troubleshooting

| Symptom | Check |
|---|---|
| No discovery card | HACS integration installed and HA restarted; app started afterward. Manual Add also checks announcements. |
| Bridge disconnected | App running, bridge address and port reachable. |
| Device visible but offline | Active proxy in range, a free slot and no other Bluetooth app holding the device. Shelly-only coverage cannot connect. |
| Device not found | Device advertising, IntiHA on, supported upstream BLE profile. Search can refresh cached advertisements. |
| Reconnect takes time | Device advertising again, active proxy availability and free slots. |
| Device reconnects but stays stopped | Expected; send a fresh command. |
| HA sliders unavailable | Device not ready, master off, or external application owns control. |
| App exits | Inspect logs and check port conflicts. |

The repository includes automated HA/Rust tests and CI builds for both
architectures. Hardware acceptance instructions and executed validation are
in the repository's `docs/` directory. For implementation details, see
[BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md).
