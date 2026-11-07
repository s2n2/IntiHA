# IntiHA bridge protocol 1

The integration connects out to `ws://APP_HOST:12346/bridge`. No token or
authentication header is required. Exactly one bridge is accepted. Both
listeners accept connections from the local network.
Each text frame is one JSON object; binary frames are rejected. Maximum frame
size is 64 KiB. Byte data is always a JSON array of integers 0–255.

HA first sends:

```json
{"type":"hello","protocol":1,"enabled":true,"auto_reconnect":true,"manual_timeout_seconds":60,"blocked_addresses":[]}
```

The engine replies `{"type":"hello","protocol":1,"version":"0.3.0b21"}`.
The socket establishes a new generation; previous pending RPCs and BLE sessions
must never survive it. `connection_id` is a new UUID for each physical connect.
The address is uppercase. No command is replayed after reconnection.

## HA to engine advertisements and events

```json
{"type":"advertisement","address":"AA:BB:CC:DD:EE:FF","name":"Device","service_uuids":[],"manufacturer_data":{"123":[1,2]},"rssi":-60,"source":"proxy-id"}
{"type":"notification","address":"AA:BB:CC:DD:EE:FF","connection_id":"uuid","characteristic":"uuid","data":[1,2]}
{"type":"disconnected","address":"AA:BB:CC:DD:EE:FF","connection_id":"uuid","reason":"connection lost"}
```

Advertisements may include `service_data` and `connectable`. Send all
advertisements continuously while enabled. Every device recognized by the
official protocol matcher is automatically eligible to connect unless its
address is in HA's persistent `blocked_addresses` deny list. This is a per-device
opt-out, not an approval list; newly discovered supported devices default to allowed.
Unsupported advertisements are ignored. The engine
queues a `discovered` event before issuing that address's first connect request,
so HA can create the device and admit the connection in the same session.
Search refreshes cached advertisements and retries previously attempted
devices; it is not required for first discovery.

## Engine to HA hardware requests

```json
{"type":"request","id":1,"op":"connect","address":"AA:BB:CC:DD:EE:FF","connection_id":"uuid"}
{"type":"request","id":2,"op":"write","address":"AA:BB:CC:DD:EE:FF","connection_id":"uuid","characteristic":"uuid","data":[0],"response":true}
```

Operations: `connect`, `disconnect`, `read`, `write`, `subscribe`,
`unsubscribe`, `set_stop_plan`, `prepare_stop`, and `scan`. GATT operations contain address and
connection_id. `read`, `subscribe`, and `unsubscribe` contain `characteristic`.
`scan` contains `enabled` and does not have a device address.

`prepare_stop` (requires both components >= 0.2.4) contains `writes`, a list
of 1 to 16 `{"characteristic":"uuid","data":[0],"response":true}` entries.
The engine obtains these from the official driver's stop operation in an
unready, packet-capture phase, or from its strictly matched classic We-Vibe
stop definition. Packet capture itself never verifies a stop or enables
controls. Other protocol payloads are not guessed.

Under the device's operation lock, HA validates every characteristic, byte
array and write mode; caches the plan for cleanup; writes it to this exact
connection; and returns `{"verified":true,"connection_id":"uuid","write_count":1}`
only after all writes complete and that session is still current. The engine
checks all three fields before setting `ready`. Failure closes the link after
attempting the cached stop. HA keeps the cached plan for master-off, bridge
loss and shutdown. The old `set_stop_plan` operation remains accepted for
compatibility but is not sufficient to make a 0.2.4 engine mark a device ready.

```json
{"type":"response","id":1,"ok":true,"result":{"services":[{"uuid":"uuid","characteristics":[{"uuid":"uuid","handle":1,"properties":["read","write","notify"]}]}]}}
{"type":"response","id":2,"ok":true,"result":{}}
{"type":"response","id":3,"ok":false,"error":"The device is not connected"}
```

Read result is `{"data":[0,1]}`. `prepare_stop` has the explicit acknowledgement
above; other successful operations return `{}`.
`disconnect` first tries the cached stop plan and then closes the radio link.
If a stop write fails it must return `ok:false` **after** closing the link;
an unsuccessful stop must never be hidden by a successful disconnect.
Connect timeout
is 50 seconds; GATT requests and stop verification allow 40 seconds for the
HA I/O deadline (10 seconds), bounded cleanup (4-second stop and 5-second
disconnect), two bounded lifecycle notifications (5 seconds each), and the
bounded response send (10 seconds). A connect performs one backend attempt per engine retry; no device operation can be queued for
a different connection. Notifications and disconnect callbacks carry the
original connection_id. HA must reject an unknown/stale connection_id.

## HA administration

```json
{"type":"admin","id":10,"op":"configure","enabled":true,"auto_reconnect":true,"manual_timeout_seconds":60,"blocked_addresses":[]}
{"type":"admin","id":11,"op":"scan","seconds":30}
{"type":"admin","id":12,"op":"stop"}
{"type":"admin","id":13,"op":"status"}
{"type":"admin","id":14,"op":"command","connection_id":"current-connection-uuid","message":{"ScalarCmd":{"Id":1,"DeviceIndex":0,"Scalars":[{"Index":0,"Scalar":0.25,"ActuatorType":"Vibrate"}]}}}
```

Commands use Buttplug **v3** messages (one object, not the surrounding array).
Movement commands must also contain the current top-level `connection_id`
from the device inventory. A command for a previous connection is rejected
even if the official engine reuses its `DeviceIndex`.
The engine uses the official v3 parser and converts through the official v4
engine. Command results are always `result.message`:

```json
{"type":"admin_result","id":14,"ok":true,"result":{"message":{"Ok":{"Id":1}}}}
{"type":"admin_result","id":14,"ok":false,"error":"An external client owns control"}
```

The `command` operation allows scalar/rotation/linear control, sensor reads,
device lists, and stop. Raw GATT and handshake messages are not permitted.
When an external client owns control, HA may still read status/sensors or stop.
Manual commands expire after `manual_timeout_seconds` (15–600; default 60).
Changing configuration to disabled stops and disconnects devices. Enabling
does not resume prior output. `blocked_addresses` contains up to 256 uppercase
Bluetooth addresses that must remain discoverable but must not be scheduled for
new connections; a newly blocked live session is safely stopped/disconnected.

Explicit stop, the manual timeout, client loss and off fence the old session,
apply the cached stop through HA and disconnect. This intentionally clears
old queued output. With auto-reconnect enabled the device reconnects stopped;
otherwise Search starts a fresh connection. A v4 feature-specific output stop
conservatively stops and reconnects that whole device. A successful response
means the transport completed its stop procedure; physical output cannot be
confirmed for devices that provide no output feedback or write acknowledgement.

## Engine events

* `status`: `enabled`, `bridge_connected`, `client_connected`,
  `connected_clients`, `scanning`, `version`, and `last_error`.
* `discovered`: `address`, `name`, `protocols` (matching upstream protocol
  identifiers), optionally advertisement signal/source fields.
* `device_status`: `address`, `connection_id`, `phase`, `error`. Phases are
  connecting, initialising, verifying_stop, retrying and ready. These are
  diagnostic; only the inventory ready flag enables HA controls.
* `devices`: `devices`, an array of v3 DeviceList entries augmented with
  `address`, `connection_id`, and `ready`. Refreshes on each inventory change.
* `output_clear`: optional `address`; clear cached control values for that
  address or all addresses if absent. It is not proof of physical stop.
* `sensor`: `message`, an upstream v3 sensor event.

## External Buttplug connections

`ws://APP_HOST:12345/` or `ws://APP_HOST:12345/buttplug`; no token or
custom header is required. Exactly one external controller. Standard upstream versions 0–4 are negotiated by
the official serializer. HA manual motion is blocked while it owns control.
The server advertises a 20-second protocol ping deadline. Socket loss, timeout,
off, and controller change stop devices and clear manual state. There is no
automatic movement replay. HTTP `/health` on both ports is process health only
and stays healthy while the bridge is absent or disabled.

When a ready device connection disappears, the external controller is closed.
Its public protocol does not carry a connection generation, so this requires a
fresh controller handshake before it can send output to a reconnected device.
An explicit device stop can therefore also end the external control session.

## Engine environment

`INTIHA_BIND` (default `0.0.0.0`), `INTIHA_CLIENT_PORT` (`12345`),
`INTIHA_BRIDGE_PORT` (`12346`), and `RUST_LOG` (default
`warn,intiha_engine=info`). No user-supplied secrets are required. The bridge
and client listeners bind different ports. A new process waits disabled until
HA sends its current enabled setting in the hello. A newly installed
integration enables discovery by default; an explicitly switched-off
integration retains its off state.


## Private intensity telemetry (0.3.0b20)

Accepted vibration commands (including those from external clients) may yield a best-effort private bridge event such as:

```json
{"type":"output_level","address":"AA:BB:CC:DD:EE:FF","connection_id":"session-id","levels":[{"index":0,"percent":45}]}
```

Only the current BLE session is accepted. Events are capped to about 4 Hz per device with zero values forwarded immediately. The event is not part of the public Buttplug protocol; it must never alter Buttplug ServerInfo, DeviceList or Ok responses. Command telemetry is not motor feedback. STOP events continue to use the existing `output_clear` message.
