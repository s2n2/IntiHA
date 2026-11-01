# Bluetooth security and specification review: IntiHA 0.2.7

Date: 1 November 2026. Baseline: the supplied 0.2.6 source, reconstructed from
0.2.0 and the subsequent published patch files. This is a focused review of
the relevant specification and transport paths, not a claim to have tested
every device or audited every upstream protocol implementation.

## Evidence from the supplied logs

The most decisive sequence is the same connection ID reaching `initialising`,
then `verifying_stop`, followed at 2026-11-01T10:22:43.643556Z by a GATT error
for handle 27: ATT error 15, `Insufficient encryption`. That establishes a
security rejection of that write. It does NOT establish that all previous
connection failures had the same cause. Handle 27 is an attribute handle on
that session's GATT database, not a universal Bond characteristic identifier.

Other errors belong to different layers:

| Message | Layer and evidential limit |
|---|---|
| `ESP_GATT_CONN_TERMINATE_PEER_USER` | The remote Bluetooth peer terminated the connection; it does not identify the reason in the device's firmware. |
| `Insufficient encryption` | ATT access requires adequate link encryption. A successful link alone is insufficient. |
| `Authenticated connection not ready ... HOST_RESO...` | HA's network/API connection to the ESPHome proxy is not ready. This is not the Bond's BLE pairing state. |
| `Home Assistant bridge generation changed` | An HA-to-IntiHA Engine WebSocket session was replaced. Pending operations from the previous session were revoked. |

Repeated peer termination can still involve firmware, ownership, stale state,
proxy availability, or radio faults. This patch does not pretend the HCI error
proves an encryption fault, and does not remove epoch protection.

## What the requested Buttplug sources actually specify

The protocol's Hardware Limitations section explicitly leaves pairing to the
OS/transport layer. The architecture separates application messages, hardware
communication managers and individual vendor protocol implementations.
Discovering a device and producing valid motor command bytes do not create an
encrypted BLE connection.

The Stop message specification defines stopping outputs. IntiHA's requirement
to cache a reusable stop plan and verify a real write before enabling controls
is an additional local safeguard, not a Buttplug protocol requirement. It
remains in place. A completed write is not independent motor-state feedback.

The public classic We-Vibe protocol description must not be treated as a
universal Bond packet format. IntiHA continues to use the engine's pinned
upstream driver/configuration to generate and address commands. This patch
adds no replacement motor/stop packet and no hard-coded handle 27.

Sources reviewed:

- https://buttplug.io/docs/dev-guide/
- https://buttplug.io/docs/dev-guide/architecture/server-in-depth/
- https://buttplug.io/docs/spec/
- https://buttplug.io/docs/spec/architecture/
- https://buttplug.io/docs/spec/device_discovery/
- https://buttplug.io/docs/spec/identification/
- https://buttplug.io/docs/spec/stop/
- https://buttplug.io/docs/dev-guide/cookbook/connections/ping/
- https://buttplug.io/stpihkal/protocols/we-vibe/

## What the supporting transport sources establish

Bleak exposes a `pair` constructor option. The inspected ESPHome backend
passes this through its connection path, requests pairing via ESPHome's API,
and requires the proxy's PAIRING capability. ACTIVE_CONNECTIONS and PAIRING
are distinct feature flags. A controller that reports connection slots is not
therefore proven to support every security operation.

The inspected retry connector has a separate `MAX_TRANSIENT_ERRORS = 9`.
`ESP_GATT_CONN_TERMINATE_PEER_USER` is classified as transient, while the
ordinary `max_attempts` argument applies to a different counter. Consequently,
seeing nine attempts does not prove an old IntiHA integration is installed.
Its 'Interference/range; External Bluetooth adapter...' suffix is generic
advice appended to several abort errors, not a measured RF diagnosis.

The public source versions retrieved are reference implementations, not proof
of the exact installed dependency versions in the user's HA runtime. The
actual supplied IntiHA source was inspected directly. Full review of the exact
pinned upstream Bond driver source was not possible from the retrieved public
pages; no device packet format has been invented to fill that gap.

Supporting primary sources:

- https://bleak.readthedocs.io/en/latest/api/client.html
- https://bleak-esphome.readthedocs.io/en/latest/architecture.html
- https://raw.githubusercontent.com/Bluetooth-Devices/bleak-esphome/main/src/bleak_esphome/backend/client.py
- https://raw.githubusercontent.com/Bluetooth-Devices/bleak-retry-connector/main/src/bleak_retry_connector/__init__.py
- https://developers.home-assistant.io/docs/bluetooth/
- https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32/api-reference/bluetooth/esp_gatt_defs.html

## Exact patch behaviour

1. Recognise a Bond only when its advertised name and the engine-provided
   `wevibe-8bit` protocol match. Request pairing for that identified device.
   This narrow IntiHA policy is justified by the observed ATT security denial;
   it is not a declaration that every We-Vibe device must be paired.
2. For other devices, an explicit ATT authentication/encryption rejection
   teaches a persisted pairing-required boolean. Error causes are inspected
   because the ESPHome backend can wrap its original typed GATT exception.
3. The selected HA-managed backend receives `pair=True` on the next fresh
   connection. No local Bluetooth adapter is paired behind the proxy's back.
4. Pairing alone never marks the device ready. Official device initialisation
   and a successful stop write on that same connection remain mandatory.
5. A failed movement write is never replayed. An ATT denial closes the failed
   session. The engine's existing retry/backoff schedules a fresh one, which
   pairs and runs the normal stop verification again. We deliberately avoid a
   long pairing operation inside the stop RPC's shorter I/O deadline.
6. A tracked client converts the first backend connection failure into a
   bridge error that the helper does not retry internally. The engine remains
   the retry owner. Cleanup still runs and cancellation still propagates.

The overall connection deadline remains 40 seconds and GATT operation deadline
remains 10 seconds. This is not a longer-timeout workaround. It does not add
an automatic unpair/reset action or a bypass for failed security negotiation.

The new `ble_pairing_required` attribute is policy only. BLE keys remain with
the relevant controllers/backends; IntiHA stores neither keys nor a fictitious
live encryption sensor.

## Roaming boundary

Pairing on one Bluetooth controller does not give this code a shared identity
or a shared key store across ESPHome proxies. Switching proxies can require
another security negotiation and may depend on the peripheral's supported
bonding behaviour. This patch does not implement key sharing or seamless
handover. First establish and retain a working encrypted session; multi-proxy
reconnection must then be tested separately. Do not keep multiple competing
controllers actively trying to connect during that initial test.

## Validation and remaining limitations

84 isolated Python tests pass using the actual changed transport modules with
HA imports and BLE radio calls replaced by test doubles. Five of those tests
execute production coordinator methods extracted from the source AST to check
security policy and persistence. Three additional normal HA coordinator tests
were added but require the full HA test runtime and were not run locally.

The isolated tests exercise pairing before protected operations, unsupported
pairing, pairing failure/cancellation, no command replay, unchanged stop
verification, one-attempt ownership, ATT error classification, and persisted
security requirements. They do not validate an actual ESPHome proxy, encryption
on the wire, physical Bond behaviour, HA loader availability or actual upstream
retry-library execution. The retry-loop test explicitly models its documented
separate transient counter.

Run `python tools/run_offline_transport_tests.py` for the limited test suite.
Run normal GitHub CI before release. No Rust code or app image changed. A live
Bond result is still needed to determine whether security negotiation fixes
the current connection path or exposes an upstream proxy/peripheral issue.
