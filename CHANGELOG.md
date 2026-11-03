# Changelog

## 0.3.0b1

Breaking architectural preview. This is NOT a claimed fix for the garage
proxy's physical BLE failures and does not yet implement proxy motor control.

### New architecture
- Replace the custom Rust engine and bespoke BLE bridge with a thin Buttplug v3
  WebSocket client for native Intiface servers.
- Add a distinct `intiha_native` HA app, building the unmodified published
  Intiface Engine 4.1.0 with its locked dependencies and native BlueZ support.
- Separate optional selected-proxy pairing/service diagnostics from device control.
- Preserve session-local device indices and stop acknowledgements honestly;
  remove cached stop-packet interception and vendor byte substitutions.

### Client behaviour
- Always-loaded master/status entities, bounded request correlation and heartbeat.
- Per-device stop preparation; one failed device does not block another.
- No movement replay or restoration after disconnect, restart or index reuse.
- Stop bypasses queued movement; stale queued commands are rejected.
- Default 60-second HA command timer, configurable between 5 and 600 seconds.
- Explicit errors, server disconnect reason and negotiated heartbeat diagnostics.
- Dynamic device entities and battery reads; ambiguous identical devices are blocked.

### Migration and validation
- Existing 0.2 entries migrate disabled and require a new native-server URL.
- Old engine app is retired; new app uses a different slug and port 12347.
- New source/tests, real localhost WebSocket simulations, Docker/native-protocol
  CI and Home Assistant import/setup CI. The latter require execution outside
  the authoring environment. See docs/VALIDATION.md for measured results.

## 0.2.7
Pairing policy and single retry-owner work in the former custom transport.

## 0.2.6
Rollback of automatic active-scanning sweeps.

## 0.2.5
Active scan discovery changes; reported availability and connection regressions.

## 0.2.4
Custom connection lifecycle and stop-preparation rewrite; hardware reliability
was not established.

## 0.2.3
We-Vibe stop preparation hotfix.

## 0.2.2
Collect dependency licences during Docker build instead of requiring a missing
build-context directory.

## 0.2.1
Packaging and HACS release-history changes.

## 0.2.0
Tokenless setup and automatic compatible-device enrolment.

## 0.1.0
Initial custom Rust server and HA Bluetooth transport.
