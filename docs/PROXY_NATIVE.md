# Proxy-native Bluetooth transport: 0.3.2

IntiHA 0.3.2 does **not** require a USB, PCIe or host Bluetooth adapter.

The supported runtime path is:

```
BLE device
  -> ESPHome active Bluetooth proxy (or another HA connectable adapter)
  -> Home Assistant Bluetooth manager
  -> IntiHA HACS integration BLE bridge
  -> IntiHA Proxy Engine hardware manager
  -> upstream Buttplug protocol matcher/driver
  -> Home Assistant entities / Buttplug clients
```

## Ownership boundary

Home Assistant owns scanner discovery, adapter selection, connection slots and the
actual Bleak/ESPHome GATT client. The HACS integration exposes a bounded bridge
for connect, disconnect, read, write, subscribe and unsubscribe operations.

The Rust Engine does not enumerate Linux Bluetooth hardware and does not start a
native `btleplug` scanner. It receives HA advertisements over the bridge and
implements Buttplug's hardware traits using bridge RPCs. The upstream Buttplug
configuration and protocol drivers still identify supported devices and generate
the device-specific operations.

## Safety / reconnect behaviour

A BLE connection receives a unique generation id. Reads/writes/subscriptions from
an expired generation are rejected rather than replayed after reconnect. Device
connection attempts are serialised per address and bounded. Stop preparation is
verified before a device is exposed as ready. A failed or uncertain stop remains
visible in HA diagnostics.

## Proxy requirements

An ESPHome proxy used for control must be configured with `bluetooth_proxy:\n  active: true`
and must appear in Home Assistant as connectable with a free connection slot.
Passive-only scanners can contribute advertisements but cannot carry GATT control.
