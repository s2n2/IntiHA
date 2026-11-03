# IntiHA

**0.3.0b1: native Intiface reference implementation and garage-proxy qualification.**

This is a breaking architectural preview, not another 0.2 connection patch.
It replaces the custom Rust server and HA-to-engine BLE bridge with a thin
Home Assistant client of an **unmodified upstream Intiface Engine**.

> **Important scope:** Device control in this release uses the native Bluetooth
> controller of the computer/phone running Intiface. ESPHome/Shelly proxies do
> **not** become native adapters. HA/ESPHome proxy **motor control and roaming
> are not implemented in this rebuild**. A separate, read-only HA proxy test is
> included so that the garage ESP32 D1 Mini can be qualified without the old
> Rust bridge, invented protocol packets or competing connection retries.

## What is included

| Component | Purpose |
|---|---|
| HACS integration, `custom_components/intiha` | Standard Buttplug v3 WebSocket client. On/off, discovery, server reconnect, dynamic devices, vibration sliders, stop and status. |
| New HA app, `native_engine`, slug `intiha_native` | Builds unmodified published `intiface-engine` **4.1.0** with its locked dependencies; uses native host Bluetooth through D-Bus. |
| Optional proxy qualification actions | Select exactly one HA-managed ESPHome proxy, request pairing, discover services, optionally read standard battery, then disconnect. No motor writes. |
| Tests and CI | Local protocol/core tests; separate CI jobs for the actual upstream engine image and Home Assistant runtime. Physical device qualification remains required. |

The app intentionally pins a known, documented upstream CLI release, **not a
claim to be the latest Intiface release**. The engine binary, protocols and
native Bluetooth manager are not patched. See [sources](docs/SOURCES.md) and
[validation](docs/VALIDATION.md) for the exact scope of verification.

## Quick start: use your existing phone or computer

1. Stop the **old IntiHA Engine** app and disable its automatic start/watchdog.
   Also stop any old Intiface/Buttplug HA integration that would claim the server.
2. Run normal Intiface Central on your phone or computer. Enable its WebSocket
   server on your trusted LAN. Use the server address it advertises, typically
   `ws://PHONE_OR_COMPUTER_IP:12345`. Keep the phone on the same network as HA.
   A localhost-only listener is not reachable from another machine.
3. Install this release through HACS as a custom Integration repository and
   restart Home Assistant. A preview release may require selecting beta releases
   in HACS. This package has not been uploaded to GitHub by its authoring session.
4. Add **IntiHA** in Devices & services, or choose **Configure** on the existing
   migrated entry, and enter that ordinary WebSocket address. No tokens or
   approval lists are requested. Turn **Enabled** on after migration.
5. Turn the device on in its normal connection mode. Native Intiface performs
   discovery and protocol initialisation. HA requests an ordinary stop before
   making the new device's vibration controls available.

This route needs no new Bluetooth hardware and no app build on Home Assistant.
The phone/computer, not the garage proxy, performs Bluetooth communication.
Use it to establish a baseline with the actual Bond before testing roaming.

## Alternatively: new native HA app

Add this GitHub repository to the Home Assistant App Store and install
**IntiHA Native Engine**. This is a NEW app, not an update to `intiha_engine`.
It needs a real Bluetooth controller accessible to the HA host's BlueZ service.
A VM needs a correctly passed-through native controller. An ESPHome Wi-Fi
proxy, even a normal ESP32 D1 Mini, does not satisfy this requirement.

Start the app with its default settings. Accept the Supervisor-discovered
integration if offered. Existing entries are not silently reconfigured: use
**Configure** and `ws://HOME_ASSISTANT_IP:12347`. The separate port deliberately
avoids confusion with the old engine. The app has no web interface.

The app builds upstream 4.1.0 at installation; the Docker build has not been
executed in the authoring environment. Run the included image-build CI before
publishing. [Native app documentation](native_engine/DOCS.md) describes ports,
permissions, diagnostics and the native-adapter limitation.

## Garage ESP32 D1 Mini: independent test

Leave your working ESPHome/Wi-Fi configuration unchanged. Turn IntiHA's
**Enabled** off and stop other controllers connected to the target device.
The diagnostic may establish a Bluetooth bond but never unpairs or sends motor
commands. It requires HA's ESPHome integration and a proxy with pairing support.

In Developer tools -> Actions, replace the example address below with your
device's actual Bluetooth address, then list its paths:

```yaml
action: intiha.list_proxy_paths
data:
  address: "AA:BB:CC:DD:EE:FF"
```

Then select the garage proxy by its returned source MAC (best), or by the unique
name fragment `garage`:

```yaml
action: intiha.probe_proxy
data:
  address: "AA:BB:CC:DD:EE:FF"
  source: garage
  pair: true
  cycles: 3
  read_battery: false
```

The name must uniquely match. It refuses to fall back to a different scanner.
It reports the actual source, library/ESPHome versions, service handles, elapsed
time, failure stage and cleanup outcome. The first failure ends the test. Run
`intiha.cancel_probe` to cancel; the result also appears in the Proxy probe result
sensor's `report` attribute.

**`passed_connection_test_only` does not mean motor control has passed.** The
report always keeps `protected_motor_write_verified: false`: the original ATT
15 failure was on a protected motor write, which this read-only test does not
exercise. Pairing success is not independent live-encryption attestation.

This test uses HA's registered remote-client connector and checks its backend
source before connecting. These partly internal APIs are capability-checked;
unsupported installed versions fail closed instead of silently rerouting.
For the garage proxy, record the actual **ESP-IDF version from its boot log**.
Do not infer it from the previously tested C3. See [qualification](docs/QUALIFICATION.md).

## Behaviour and limitations

- Master controls and diagnostics load even if the server is offline. Per-device
  movement controls remain disabled until that session's standard stop is acknowledged.
- Native discovery is automatic; no HA-wide active scan sweeps are added.
  Each device gets an independent preparation task, so one stalled device does
  not delay another's controls.
- Disconnects invalidate indices, requests and queued targets. Reconnect starts
  at zero, never replays movement, and requests another standard stop.
- Each non-zero HA movement request has a default 60-second control timeout
  (Configure: 5-600 seconds). A protocol heartbeat is independent of this timer.
  The supplied app configures a 10-second upstream heartbeat. External servers
  may disable it; the diagnostic reports the actual setting, including zero.
- A successful stop acknowledgement is NOT physical motor-state feedback.
  No software can deliver a stop over a radio link that has already failed.
- Only advertised vibration features and standard battery reads are exposed in
  this preview. Rotation, linear motion, manufacturer-native patterns, raw writes
  and shared/external command routing are not implemented.
- One server/control owner at a time. Turn HA's master off before using another
  Buttplug client against the same server. No multi-client mixer is claimed.
- Standard v3 inventory does not provide a universal stable Bluetooth address.
  Identities use the server display name and capabilities, not DeviceIndex.
  Give identical devices distinct display names in the upstream configuration.
  Simultaneous ambiguous twins are disabled rather than silently misidentified.
- No client tokens. The app's WebSocket is accessible on the LAN. Do not expose
  it to the internet or untrusted guests. Supervisor's automatic internal token
  is only used for app discovery, never requested from the user or passed to
  the engine subprocess.

## Updating from 0.2.x

**Back up first. Read [MIGRATION](docs/MIGRATION.md).** The old `/bridge` URL is
not a standard Buttplug client endpoint. Migration disables control and asks for
a native server address. Device-specific entity IDs will change; the old system's
Bluetooth-address IDs cannot honestly be retained using only v3 inventory.
Never run old and new controllers against the same peripheral simultaneously.

## Version history (also visible in HACS)

| Version | Change |
|---|---|
| **0.3.0b1** | Architectural rebuild: standard native Intiface client, unmodified upstream app, independent selected-proxy diagnostic, per-device tasks, no movement replay. Preview: no HA-proxy motor control. |
| 0.2.7 | Prior custom bridge: pairing policy and nested-retry changes; not a proven hardware resolution. |
| 0.2.6 | Disabled automatic HA-wide active scan sweeps; retained manual search. |
| 0.2.5 | Added active discovery sweeps; later rolled back after reported regressions. |
| 0.2.4 | Reworked custom stop preparation and connection handling. Brief ready states were observed, not sustained reliability. |
| 0.2.3 | Earlier We-Vibe stop preparation change. |
| 0.2.2 / 0.2.1 | Container licence packaging and release-history fixes. |
| 0.2.0 | Tokenless setup and automatic device enrolment. |
| 0.1.0 | Original custom HA/Rust transport prototype. |

See [CHANGELOG](CHANGELOG.md), [architecture](docs/ARCHITECTURE.md),
[publishing](docs/PUBLISHING.md), and [validation](docs/VALIDATION.md).
