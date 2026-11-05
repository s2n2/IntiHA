# Proxy-native Bluetooth transport: 0.3.0b3

IntiHA 0.3.0b3 does **not** require a USB, PCIe or host Bluetooth adapter.

The supported runtime path is:

```
BLE device
  -> ESPHome active Bluetooth proxy (or another HA connectable adapter)
  -> Home Assistant Bluetooth manager
  -> IntiHA HACS integration BLE bridge
  -> IntiHA Proxy Engine hardware manager
  -> upstream Buttplug protocol matcher/driver
  -> Home Assistant entities / external Buttplug clients
```

## Discovery and protocol matching

IntiHA does not maintain an allow-list of toy brands or protocols. Valid BLE
advertisements received from Home Assistant are cached and passed to the
upstream Buttplug `ServerDeviceManager`. Buttplug performs its normal protocol
matching, including the name, service and manufacturer-data rules in its device
configuration.

The HACS integration is told that a device is discovered only after Buttplug
accepts the connector as a viable protocol. This keeps arbitrary nearby BLE
sensors out of IntiHA while allowing the full set of Intiface-supported BLE
protocols to use the HA proxy transport.

`protocol_hints` retained by the bridge are advisory only. They may inform a
narrow transport/security policy such as known pairing behaviour, but they do
not admit or reject devices.

### Identity-poor advertisements

Some supported BLE devices do not advertise a useful local name. A classic
example is a We-Vibe Ditto that may appear to Home Assistant only as its MAC
address while advertising the short vendor service `0xBB03`. In these cases,
IntiHA compares non-standard 16-bit advertised services with the low 16 bits of
Buttplug's configured 128-bit GATT service UUIDs. Matching protocols become
bounded candidates rather than confirmed devices.

The candidate still has to connect through Home Assistant and expose the actual
service and characteristic map required by that Buttplug protocol. If that
post-connect GATT verification fails, the session is dropped and the next
ambiguous candidate may be tried after backoff. Standard adopted GATT services
(`0x1800`-`0x27ff`) never trigger this probing path.

This transport is intentionally BLE-only. Intiface devices that require a USB
serial, HID, gamepad or another non-BLE hardware manager cannot be carried over
an ESPHome Bluetooth proxy.

## Ownership boundary

Home Assistant owns scanner discovery, adapter selection, connection slots and
the actual Bleak/ESPHome GATT client. The HACS integration exposes a bounded
bridge for connect, stop, disconnect, read, write, subscribe and unsubscribe
operations.

The Rust Engine does not enumerate Linux Bluetooth hardware and does not start a
native `btleplug` scanner. It receives HA advertisements over the bridge and
implements Buttplug's hardware traits using bridge RPCs. The upstream Buttplug
configuration and protocol drivers identify supported devices and generate the
device-specific operations.

## External client handover

Opening the public WebSocket does not itself take control. Ownership transfers
only after the external client completes a valid Buttplug handshake.

When control changes hands, an external command times out, or the external
client disconnects, IntiHA sends the verified per-session stop plan while
keeping healthy BLE connections alive. That prevents a controller from leaving
an actuator running without forcing a complete proxy reconnect before the next
command.

A failed or unconfirmed stop retires the affected BLE session. Master off,
engine shutdown and other hard safety paths retain their existing disconnect
behaviour.

## Safety / reconnect behaviour

A BLE connection receives a unique generation id. Reads/writes/subscriptions
from an expired generation are rejected rather than replayed after reconnect.
Device connection attempts are serialised per address and bounded. Stop
preparation is verified before a device is exposed as ready. A failed or
uncertain stop remains visible in HA diagnostics.

## Proxy requirements

An ESPHome proxy used for control must be configured with:

```yaml
bluetooth_proxy:
  active: true
```

It must appear in Home Assistant as connectable with a free connection slot.
Passive-only scanners can contribute advertisements but cannot carry GATT
control.


## Master server switch

The Home Assistant **Server enabled** entity is the master runtime gate. Off stops outputs, disconnects BLE sessions, cancels discovery, invalidates external control and makes the public Buttplug WebSocket endpoints return HTTP 503. The HA bridge remains connected only as the private control plane required to turn the server back on.

## Device identity and per-device connection policy

Home Assistant keeps the best useful name IntiHA has learned for each physical
BLE address. A specific advertised/model name is not replaced by a generic
Buttplug label. If the device exposes no useful name, IntiHA uses a stable
protocol-family label plus the final Bluetooth address bytes so multiple generic
devices remain distinguishable. Users can still rename the HA device normally.

Each discovered supported BLE device has a **Connect to this device** switch.
It defaults on and is stored by the Home Assistant integration. Off keeps the
device visible but places its address in the engine's deny list, preventing new
GATT attempts and safely disconnecting an existing session. This is intended
for supported devices that are visible over Bluetooth but should not be owned by
this IntiHA instance, for example a nearby neighbour's device.

## Bounded shutdown

0.3.0b7 bounds Home Assistant stop-all locking, Rust shutdown cleanup and the
container entrypoint wait. Supervisor updates therefore cannot wait forever on
a wedged BLE/admin operation. When upgrading from an older build, switching the
server off before the first app update remains the cleanest path because the old
container itself does not yet contain these new bounds.
