# Architecture: 0.3.0b1

## Two intentionally separate paths

Production-control candidate:

```
HA entities -> IntiHA protocol-v3 client -> unmodified Intiface server
            -> upstream protocol driver/native Bluetooth manager -> device
```

Qualification-only path:

```
HA action -> exact registered remote-proxy connector -> pairing + GATT services
          -> optional standard battery read -> disconnect
```

No custom Rust hardware manager, bridge RPC, protocol packet rewrite, delayed
stop-packet capture or serial readiness sweep remains. This release is not
backwards compatible with the old hardware bridge. The eventual production
HA-proxy backend is deliberately absent until the lower path passes physical
qualification and a byte-/operation-parity implementation is available.

## Engine vs application client

`native_engine/Dockerfile` installs exact upstream package 4.1.0, `--locked`.
The wrapper starts the official CLI and announces its address. It does not
forward commands, act as another server client, or alter protocol definitions.
4.1.0 is a selected reference version, not the latest engine claim.

The Python client implements the standard older v3 messages for compatibility:
RequestServerInfo, RequestDeviceList, DeviceAdded/Removed, Start/StopScanning,
ScanningFinished, ScalarCmd (Vibrate only), StopDeviceCmd, StopAllDevices,
SensorReadCmd/Reading and Ping. A server that cannot negotiate v3 is rejected.
Protocol support is verified separately from Bluetooth hardware support.

## Cancellation and ownership

One WebSocket reader owns dispatch. Requests correlate by Id and never await
inside event callbacks. A request timeout closes the session: an unacknowledged
movement might have happened, so it cannot be retried safely. Stop/cancellation
invalidates queued generation tokens. DeviceIndex never identifies a physical
device across sessions. Fresh device instances receive fresh UUIDs.

Each device prepares independently using the upstream StopDeviceCmd, then
becomes ready on acknowledgement. This is a protocol-level acknowledgement,
not a cached BLE stop plan and not physical motion sensing. No one-device
stop failure holds a global inventory lock. The control timer and server
heartbeat address different failures and remain separate.

Turning the master off attempts StopAllDevices, closes the connection and
releases the client slot. Another application must take ownership explicitly.
No generic multi-client blending or TCP proxy is hidden in the BLE path.

## Identity limit

V3 does not provide a universal stable MAC/address for every device. HA keys
are derived from integration namespace, model, upstream display name and motor
capabilities. This survives server DeviceIndex changes for a distinguishable
device. It cannot distinguish two physically identical devices used at different
times without distinct upstream display names. Simultaneous duplicates are
blocked. Changing display names/capabilities can produce a new HA device.
We do not silently retain the old bridge's MAC-based unique IDs.

## Proxy qualification boundary

The action resolves a source from HA's already observed remote scanners and
uses that scanner's connector factory directly. It checks the returned backend
source. The generic HA Bleak wrapper can select another adapter, so it is not
used to make a claim about one specific proxy. This intentionally depends on
capability-checked backend APIs; it is not a new permanent control backend.

No GATT writes are issued. Pairing may establish a bond, but no key is extracted,
shared or reset. Handles are exported for diagnosis only, not reused for control.
Services, pairing and link establishment are grouped in the backend's actual
connect method; the report does not invent separate timing events it cannot see.

A passing read-only test does NOT prove that the Bond's protected motor write
will accept the resulting link. That requires a subsequent parity test with the
verified upstream driver, after firmware and reconnection behaviour are qualified.
