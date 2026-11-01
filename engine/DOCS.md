# IntiHA Engine

IntiHA connects compatible Bluetooth devices through **Home Assistant's
Bluetooth network**, then exposes them to applications that use Buttplug.
The engine uses the official Rust Buttplug server and its device protocols.

Two components work together:

1. **IntiHA integration**, installed through HACS or copied into
   `custom_components/intiha`. It supplies Home Assistant entities, options
   and the Bluetooth connection to your existing active proxies.
2. **IntiHA Engine**, this app. It supplies the Buttplug server and device
   protocol support. Home Assistant Container users can run the same engine
   with Docker Compose.

Both are required for control through Home Assistant proxies. Installing the
engine alone does not give it access to Home Assistant's Bluetooth network.
The engine does not require a Bluetooth adapter, USB passthrough, host network,
privileged mode, a Home Assistant access token, or the Supervisor API.

## Requirements

- Home Assistant **2026.10 or later** for this initial release.
- An **active ESPHome Bluetooth proxy** or a connectable local Bluetooth
  adapter, set up in Home Assistant. Check free connection slots as well as
  signal strength. Other integrations share those slots.
- An `amd64` or `aarch64` Home Assistant OS installation for this app, or
  Docker on either architecture for the standalone engine.
- A device supported by the upstream Buttplug Bluetooth protocol catalogue.
  Upstream support does not establish that every model works through every
  proxy; test your particular devices before relying on them.

**Shelly Bluetooth proxies cannot carry the GATT control connection.** They
can contribute advertisements, but you still need an active ESPHome proxy or
local adapter near the device. A Shelly setting called active scanning does
not enable GATT proxying.

ESP32 proxies normally have three active connection slots. A continuously
connected device occupies one slot. Ethernet proxies avoid Wi-Fi/Bluetooth
radio contention and are a useful option for fixed coverage. See the
[official ESPHome proxy documentation](https://esphome.io/components/bluetooth_proxy/)
and [Home Assistant Shelly documentation](https://www.home-assistant.io/integrations/shelly/).

## Home Assistant OS installation

1. Add `https://github.com/s2n2/IntiHA` to **HACS → Custom repositories** as
   an **Integration**. Download IntiHA and restart Home Assistant.
2. Add the same repository URL to **Settings → Apps → App store →
   Repositories**. Older Home Assistant screens call these add-ons.
3. Install **IntiHA Engine**. This repository builds the app from its locked
   source dependencies. The initial build takes longer than downloading a
   prebuilt image; leave sufficient free disk space and memory.
4. In the app's Configuration tab, set two different random secrets:
   **Client token** and **Bridge token**. Each must contain at least 32
   characters, up to 256, using visible ASCII characters without spaces. A
   password manager can generate them. The following command
   generates a URL-safe, 64-character secret; run it separately for each:

   ```sh
   python3 -c 'import secrets; print(secrets.token_hex(32))'
   ```

5. Start the app. Enable **Start on boot** and **Watchdog**.
6. Add **IntiHA** under **Settings → Devices & services → Add integration**.
   Enter the engine bridge URL `ws://HOME_ASSISTANT_IP:12346/bridge` and
   the **Bridge token**. Use the actual IP address of the Home Assistant
   machine. If you changed the app's port mapping, use the mapped bridge port.
7. Switch IntiHA on and use its **Search devices** control. Put a device in
   its Bluetooth connection mode. Select the discovered device in IntiHA's
   options before it is connected. Close manufacturer apps or other servers
   that already hold the device's Bluetooth connection.

The integration remains loaded when the app, proxies or devices are offline.
Use its connection/status entities to identify what is available.

## Connect a Buttplug application

Set the application's server address to:

```text
ws://HOME_ASSISTANT_IP:12345/?token=YOUR_CLIENT_TOKEN
```

Use the **Client token**, not the bridge token. The engine also accepts
`Authorization: Bearer <client_token>` for clients that can set an HTTP header.
For tokens containing characters outside the URL-safe alphabet, URL-encode
the query value. Hexadecimal tokens generated above need no escaping.

The listener negotiates Buttplug protocol versions through the official
serializer. Only one external controller may own control at a time. Home
Assistant stop controls remain usable; Home Assistant manual movement is
blocked while an external controller owns the devices. Disconnect that
application to return control to Home Assistant.

Some applications only accept a host and port, or hard-code a URL without a
token. They cannot connect directly to this authenticated endpoint. Do not
remove authentication to accommodate them. If supported, configure an
Intiface desktop repeater with the full remote URL and connect the local app
to that repeater. Compatibility depends on the client's URL support.

A web application served over HTTPS may be blocked by its browser when it
tries to connect to a remote insecure `ws://` address. Intiface documents a
[local repeater workflow](https://intiface.com/docs/intiface-central/ui/app-modes-repeater-panel/)
for remote servers. TLS termination is also possible with a correctly
configured local reverse proxy. Tokens authorise access; plain WebSocket
transport does not encrypt them. Keep both ports on a trusted local network.

## Configuration

| Setting | Meaning |
| --- | --- |
| Client token | Random secret of 32–256 visible ASCII characters without spaces for Buttplug applications. |
| Bridge token | Different random secret of 32–256 visible ASCII characters without spaces; must match the IntiHA integration. |
| Log level | `info` normally; `debug` only when diagnosing an issue. Debug logs can contain device identifiers. |
| Port 12345 | Buttplug client endpoint. Change its host mapping if another server uses this port. |
| Port 12346 | Connection from the IntiHA integration to the engine. Change its host mapping and integration URL together. |

The app's HTTP `/health` endpoint reports that the process is responding. It
stays healthy when the integration is disabled or disconnected, so the
Supervisor watchdog does not keep restarting a healthy idle process.
Use IntiHA's Home Assistant sensors for bridge and device status.

## Moving around the house

Use overlapping coverage from active proxies. Home Assistant selects a
connectable adapter when establishing each Bluetooth connection. An existing
connection cannot jump seamlessly between radios: if it drops, IntiHA attempts
to reconnect through the available Bluetooth network.

There can be a pause while a failed connection is detected and a new
connection is established. Battery level, device firmware, whether the device
advertises again, walls, nearby radios and free proxy slots all affect recovery.
Repeatedly changing adapters based only on RSSI would introduce extra
disconnects, so IntiHA leaves a working connection in place.

Reconnection restores connectivity, **not the previous output**. Old commands
are discarded and the device is stopped before new control is admitted.
Send a fresh command after reconnection. On connection loss, an unreachable
device may retain its last output until it receives another command or is
stopped using its own controls. No software can deliver a stop command through
a broken radio link.

Explicit stop controls also release the affected Bluetooth connections after
attempting their cached stop commands. This prevents an old queued command
from restarting a device. With automatic reconnection enabled, the devices
reconnect in a stopped state; allow a short pause before sending another
command. With automatic reconnection disabled, use Search devices to connect
them again. A controller change may produce the same short connection pause.

For the classic We-Vibe protocol, IntiHA suppresses the exact known upstream
initialisation pulse and replaces it with the protocol's stop packet. This is
a narrow exception for that specific packet, not a promise that every device
firmware has no connection-time behaviour. Native We-Vibe pattern selection
is not exposed by the standard upstream driver in this release.

## Home Assistant Container / standalone Docker

Install the IntiHA integration in Home Assistant. Clone or download this
repository on the Docker host, then create a private `engine/.env` file:

```dotenv
INTIHA_CLIENT_TOKEN=replace_with_one_random_64_character_hex_secret
INTIHA_BRIDGE_TOKEN=replace_with_a_different_random_64_character_hex_secret
```

Replace both example values before starting. Keep this file out of Git.

```sh
cd engine
docker compose up -d --build
```

Point the IntiHA integration at `ws://DOCKER_HOST_IP:12346/bridge`. External
clients use the same Docker host IP with port 12345 and the client token.
The container requires outbound network access only for the source build;
at runtime it communicates with the integration and local clients.

The image also supports these environment variables when run without the
Supervisor options file:

| Variable | Default |
| --- | --- |
| `INTIHA_CLIENT_TOKEN` | Required |
| `INTIHA_BRIDGE_TOKEN` | Required and different from client token |
| `INTIHA_BIND` | `0.0.0.0` |
| `INTIHA_CLIENT_PORT` | `12345` |
| `INTIHA_BRIDGE_PORT` | `12346` |
| `INTIHA_LOG_LEVEL` | `info` |
| `RUST_LOG` | Derived from log level; optional advanced override |

## Troubleshooting

| Symptom | Check |
| --- | --- |
| App refuses to start | Both secrets are present, at least 32 characters, and different. Check whether another app already uses the mapped ports. |
| Engine online but bridge disconnected | Bridge URL, mapped port and bridge token; the integration must be installed and configured. |
| Device visible but cannot connect | An active proxy is in range with a free slot. Shelly-only visibility is insufficient. Close competing Bluetooth apps. |
| No devices found | Device powered on and advertising; integration enabled; active proxy present. Unsupported devices will not appear as supported candidates. |
| Reconnect takes time | Device must advertise again. Check proxy availability, connection slots and placement. |
| Device reconnects but stays stopped | Expected. Send a fresh command; prior output is not replayed. |
| Home Assistant movement control rejected | An external client currently owns control. Disconnect it first. Stop remains available. |
| Browser cannot connect | Browser mixed-content restrictions, full token-bearing URL and client compatibility. Consider a local Intiface repeater. |

## Verification and reporting

The repository includes automated protocol, bridge and lifecycle tests, plus
CI jobs for Python, Rust, HACS metadata, hassfest and app image builds for
both supported architectures. Tests with simulated Bluetooth do not establish
physical compatibility or roaming reliability in your house.

Before relying on a setup, test one device at low output, then check disable,
app restart, Home Assistant restart, proxy outage, client disconnect and
moving between rooms. Confirm that old output is not replayed after recovery.
Record device model, firmware, proxy model/firmware and relevant software
versions when reporting a problem. Remove tokens and personal device
identifiers from shared logs.

For implementation details, see [BRIDGE_PROTOCOL.md](BRIDGE_PROTOCOL.md).
