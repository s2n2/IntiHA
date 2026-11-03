# Garage proxy and native-path qualification

The user's garage proxy is a normal ESP32 D1 Mini. Its actual ESPHome/ESP-IDF
versions must be read from that proxy's boot log. Previously observed C3
versions do not establish the garage firmware. No C3-specific YAML change is
part of this rebuild.

## Test A: native reference

Use normal Intiface on the existing phone/computer and this HA client. Establish
connect -> stop acknowledgement -> small commanded output -> stop, under direct
supervision on a bench. Check real stop behaviour, not only the UI. Repeat
power cycles and warm reconnects. Keep the exact upstream server version in
results. A passing simulated test is not a result for this test.

## Test B: one explicitly selected HA proxy

Disable the HA native-control master and stop other Bluetooth controllers.
Use `intiha.list_proxy_paths`, then `intiha.probe_proxy` with `source: garage`
or its exact source address, `pair: true`, `cycles: 3`. Keep the Bond in normal
pairing mode. The probe stops on the first failure; it is not an endless retry
loop. A fully successful run reads `passed_connection_test_only`.

The probe never writes motor characteristics or automatically deletes bonds.
`pairing_completed` means the backend's pairing request returned; it is not
live encryption attestation. `protected_motor_write_verified` remains false.
Its services/handles report belongs to that session only.

Errors before connection, during connect/pair/services, during battery read and
while disconnecting are recorded separately where the backend exposes them.
The backend combines connect, pair and service discovery, so this release
reports that combined stage honestly. An unsupported pair API/capability is a
qualification failure, not a reason to bypass security.

## Test C: controlled firmware comparison, only if evidence warrants

Record actual SDK versions before changing firmware. Upstream issue reports
about encrypted reconnect regressions are hypotheses, not proof of this Bond
failure. Compare a compatible pinned ESPHome/SDK pair on the **same garage
board**, keeping IntiHA, device and physical placement constant. A reboot-only
success is a useful observation, not the desired final operating mode.

## Next production proxy milestone (not shipped)

Only after the selected backend is qualified should a minimal upstream hardware
manager be added. It must preserve actual service + characteristic identity,
write-with-response flags, payload bytes, reads and notifications. A parity test
must compare native and proxy paths with the same driver/configuration release,
including the protected stop write. It must not return simulated write success
to capture packets or serialize all devices behind one preparation loop.

## Acceptance matrix (targets, not results)

- Twenty cold starts and twenty warm reconnects on the same proxy without reboot.
- Pairing failure, unavailable proxy and sleeping device are distinguishable.
- One failing device does not prevent a second device becoming ready.
- HA restart, engine restart, proxy API loss and BLE loss each have bounded cleanup.
- No old movement command is restored or replayed after reconnect.
- Motor stop is physically observed during supervised tests; failure is recorded.
- Only then test a second proxy, bond behaviour and room-to-room reconnection.

Do not claim seamless handover or shared bond keys: this build implements neither.
