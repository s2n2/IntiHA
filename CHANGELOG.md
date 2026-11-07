## 0.3.0b19 - Optional classic We-Vibe device feedback
- Poll the documented 8-byte We-Vibe GATT info characteristic over the *existing* Home Assistant BLE client, only after a verified STOP and only when the correct service and read property are present.
- Add **Device-reported intensity** (0-100%) and **Device-reported pattern** per supported device. Each reading includes internal/external motor settings, timestamp, and Bluetooth address.
- Poll at about one read per second; skip reads while a control operation owns the BLE lock, bound each read to 1.25s, and stop telemetry on repeated read failures. Never disconnect or alter output solely because optional telemetry fails.
- Unavailable means no fresh device feedback. Commanded intensity remains separate. This reads device firmware settings, not physical motor motion; unsupported hardware is never guessed.
- Do not change external Buttplug messages, STOP verification, engine control, or Ogg's command/watchdog path.

## 0.3.0b18 - per-device commanded vibration intensity sensors
- Add a per-device **Commanded intensity** sensor (0-100%) using the highest vibration actuator command; multi-motor values are also available in `vibration_channels` attributes.
- Capture accepted ScalarCmd, VibrateCmd and SingleMotorVibrateCmd commands from external clients (including Ogg) using a **private engine-to-Home-Assistant bridge event**. Public Buttplug v3 DeviceList, handshake, command results and events remain unchanged.
- Publish best-effort updates at most four times per second per device; send zero-output updates promptly and keep telemetry off the blocking Bluetooth-control path.
- Match telemetry to the current BLE connection ID. Set the sensor unavailable while disconnected and reset the displayed command to zero on a confirmed stop/reconnection.
- Preserve external client output telemetry when ordinary engine status messages arrive. Do not interfere with stop verification, automatic reconnection or the Ogg pulse watchdog.
- This sensor reports **commanded intensity, not actual motor speed**; firmware-native patterns that run internally without new commands cannot be measured by this sensor.
- Frequent sensor updates can increase Home Assistant recorder history. Consider excluding the commanded intensity sensors from Recorder if they are used only for a live dashboard.

## 0.3.0b17 - restore standard Buttplug server compatibility
- Revert the b16 external WebSocket MAC/model-name enrichment entirely.
- Forward Buttplug ServerInfo, DeviceList, DeviceAdded, Ok, Error, and all events without editing their JSON. This removes the suspected cause of Ogg looping between Searching and Connecting.
- Retain HA-side model recognition, b16's shorter BLE connect timeouts, and stop-verified recovery logic.
- MAC-based matching in Ogg remains available for other servers that expose physical addresses; IntiHA's public Buttplug messages no longer supply an extra MAC.
- No Ogg integration changes required; Ogg v0.1.7 is compatible with standard Buttplug v3.
- Full Rust/Docker compile and live BLE test are still required.

## 0.3.0b16 - reconnect safety, identity and shorter timeout
- Expose the stable BLE MAC address and best-known model name to external Intiface/Buttplug clients including Ogg (extra `BluetoothAddress` field and `DeviceDisplayName`).
- Preserve specific advertised/device identity and prevent generic "We-Vibe" names replacing better names.
- Reduce connect/pair/service discovery time to a bounded 16s and ordinary engine GATT operation RPC wait from 40s to 25s, while retaining verified stop safety.
- Never automatically replay old actuator output on the IntiHA side: the external client Ogg decides whether its still-valid Run/Pulse command should resume after a verified stop.

- On loss of any ready Bluetooth connection, revoke the former output generation, including a Home Assistant manual-control session.
- A new connection must receive the existing verified safe-stop handshake before becoming controllable.
- Output commands from the old connection are not resumed automatically.
- A stop cannot reach hardware while the Bluetooth link is disconnected.

## 0.3.0b14 - known-device reconnection priority

- Keep the engine updated with the set of known, enabled Bluetooth devices.
- Prioritise reachable known devices over unknown discovery candidates in each connection batch.
- Continue automatic reconnection on newly observed advertisements, including when devices move between proxies or are switched back on (requires an available connection slot and automatic reconnection enabled).
- Stop configuration updates from resetting failure cooldowns, which could otherwise cause repeated failed connection attempts.
- Keep the per-device Connect to this device switch authoritative; disabled devices remain blocked.
- Preserve stop verification and zero-output-on-reconnect behaviour; never replay a previous output.
- No forced active scans during background reconnection; the manual Search button remains available for new-device discovery.

**Validation:** Python syntax and repository metadata checks are possible offline. A full Rust/Docker build and live ESPHome proxy validation are required before release to production.

## 0.3.0b14 - device connection grouping

- Automatically assign each known device exactly one IntiHA label: **Currently connected**, **Known, not connected**, or **Connection disabled**. Labels update whenever the device connects, disconnects, or its Connect to this device switch changes. Existing custom device names, identifiers, and unrelated labels are preserved.
- Add a **Connection disabled** count/list sensor. Make the existing **Known but not connected** sensor exclude disabled devices so the three categories are mutually exclusive.
- NOTE: Home Assistant controls the heading and presentation of the integration's native device list; an integration cannot split that particular list into custom sections. Use the three labels as device filters, or the three sensors to build dashboard sections.

# Changelog

## 0.3.0b14

- Avoid repeated BLE connection attempts when a Home Assistant proxy has no available connection path. The HA bridge now applies a per-device 60-second cool-down for unavailable proxies and 45 seconds after connection timeouts, with less aggressive handling of other failures.
- Identify each device's most recent failure as waiting for proxy, waiting for advertisement, connection timed out or retrying in its Home Assistant status sensor.
- Preserve b11 engine and safety-related command handling, manual connection switches, device names and connected/disconnected summaries. No automatic replay of actuator commands.
- Keep Supervisor local Docker builds. Do not try to pull unpublished images from GHCR.
- Note: install-progress UI and Supervisor orchestration cannot be controlled by the integration; a successful image build can still appear stuck until Supervisor/Core have completed their own state transitions.

## 0.3.0b11

- Build-recovery release: revert the unverified Rust stop-barrier changes introduced in b10 and restore the b8 engine command implementation.
- Keep b10 connected/disconnected device summary sensors, device naming and per-device connection controls.
- Fall back to Supervisor source builds while image publication is unverified.
- This release does NOT fix the post-pulse restart issue. It prioritises restoring a buildable baseline.


## 0.3.0b11

- Fix external-client stop semantics so Buttplug clears its internal output/keepalive state before IntiHA sends the verified physical BLE stop.
- Add a per-session output generation barrier so queued movement/keepalive writes created before a stop cannot run after the stop and restart a device at the previous level.
- If the official stop state cannot be cleared, retire the affected BLE session after sending the verified stop rather than risk latent output.
- Add separate Currently connected and Disconnected devices summary sensors with device names/addresses in attributes.
- Retain b9 prebuilt GHCR app images, persistent device names, and per-device Connect to this device policy.

## 0.3.0b9

Prebuilt app images and reliable Supervisor updates, retaining the b8 identity fixes.

- Publish amd64 and aarch64 Engine images to `ghcr.io/s2n2/intiha-engine` on pushes to `main`, then publish a multi-architecture manifest tagged with the Home Assistant app version.
- Add the app `image:` setting so Supervisor pulls a prepared container instead of compiling the Rust engine locally during every update.
- Add a 30 second Docker action timeout so normal shutdown has enough time to complete cleanly.
- Fix the release workflow's Cargo/Home Assistant version comparison (`0.3.0-b9` internally vs `0.3.0b9` externally).
- Retain b8 persistent best-known device names, active-scan name recovery, Device Name GATT recovery, and the per-device **Connect to this device** switch.

## 0.3.0b6

- Add generic proxy-native BLE advertisement enrichment for identity-poor devices that advertise only a short vendor service UUID.
- Match short vendor service fingerprints against Buttplug's own configured GATT service maps rather than maintaining an IntiHA device-name list.
- Rotate ambiguous protocol candidates across bounded reconnect attempts.
- Require the real post-connect GATT services and characteristics to match before a service-alias candidate is accepted.
- Specifically fixes classic We-Vibe devices such as Ditto advertising `0000bb03-0000-1000-8000-00805f9b34fb` without a usable local name.
- Exclude standard adopted GATT services from alias probing to avoid connecting to ordinary BLE peripherals.

## 0.3.0b5

- Fix Rust/Cargo package versioning: Cargo now uses SemVer-valid `0.3.0-b5` while Home Assistant and IntiHA continue to report `0.3.0b5`.
- Supersedes the unbuildable 0.3.0b4 package without changing the server master-switch behaviour.

## 0.3.0b4

Master switch now gates the whole user-facing Intiface server.

- Rename the Home Assistant master toggle to **Server enabled**.
- Turning it off immediately marks the engine disabled, cancels scanning, stops outputs, disconnects BLE sessions, clears discovered/connected device state and invalidates any current external-controller ownership.
- New external Buttplug WebSocket connections to `/` or `/buttplug` are rejected with HTTP 503 while disabled. Existing external clients are invalidated by the control-epoch barrier and close on their next activity/heartbeat.
- Keep only the private Home Assistant bridge/control channel alive while disabled. This is intentional: without that channel the same Home Assistant toggle could not turn the server back on.
- Turning the toggle on immediately makes the external Buttplug endpoint available again and resumes proxy-native discovery/reconnect behaviour.
- Add an engine regression test for disabled → enabled → disabled external-server gating and control-epoch invalidation.

## 0.3.0b3

Broad Intiface BLE discovery and external-client handover fix.

- Remove IntiHA's exact communication-specifier admission filter. Every valid Home Assistant BLE candidate is forwarded to the official Buttplug `ServerDeviceManager`, which performs its normal wildcard, manufacturer-data, service and name matching.
- Only announce a device to Home Assistant after the upstream Buttplug manager accepts the connector as a viable supported protocol, avoiding false-positive HA devices while no longer hiding valid Intiface BLE hardware.
- Keep a bounded candidate cache and evict the oldest unseen candidate when full instead of permanently rejecting newly observed peripherals.
- Treat the local `protocol_hints` list as a transport/security hint only; it is never used to decide whether a device may reach Buttplug.
- External WebSocket connections no longer seize control merely by opening a socket. Ownership transfers after a valid Buttplug handshake.
- Add a verified stop-in-place bridge operation. External-client takeover, command timeout and disconnect stop outputs without tearing down a healthy proxy GATT session.
- Treat external `StopAllDevices` / `StopDeviceCmd` as actuator stops, not transport disconnects, so clients that issue routine safety stops can continue sending commands on the same proxied session.
- Retain hard disconnect behaviour for master-off, shutdown, failed/uncertain stop and stale-session safety paths.
- Detect classic We-Vibe stop handling from the specialised GATT endpoint rather than from IntiHA's pre-match protocol name, keeping device-specific safety logic after broad discovery.
- Add regression coverage for an arbitrary BLE candidate reaching the upstream matcher, verified stop without disconnect, and an external v3 client driving an existing proxied session then releasing it without reconnect.
- Scope: broad matching applies to Intiface-supported **BLE** devices reachable through Home Assistant Bluetooth. Non-BLE Intiface hardware managers (for example USB serial/HID/gamepad transports) are not provided by an ESPHome BLE proxy.

## 0.3.0b2

Proxy-native beta rebuild for Home Assistant installations without a local Bluetooth adapter.

- Restores the Home Assistant managed-Bluetooth hardware backend as the production transport.
- ESPHome active Bluetooth proxies can provide discovery and GATT connect/read/write/notify operations.
- The Engine does not open BlueZ, `btleplug`, D-Bus or a USB Bluetooth adapter.
- Home Assistant owns adapter selection and connection slots; the Engine owns Buttplug protocol matching and device commands.
- Keeps the 0.2.7 bounded connection-attempt, BLE security/pairing, safe-stop and stale-generation protections.
- Adds an explicit Bluetooth transport diagnostic and reports that a local Bluetooth adapter is not required.
- Replaces the native-only 0.3.0b1 direction for proxy-only installations.
- Fixes two invalid multi-exception clauses in the reconstructed config flow that Python 3 rejects at import time.

## 0.2.7

BLE security and connection retry correction, based on the observed Bond ATT
`Insufficient encryption` failure and a review of the Buttplug spec, Bleak,
bleak-esphome and bleak-retry-connector. This is an integration-only runtime
update; keep Engine 0.2.4 or 0.2.5. A physical resolution is not yet verified.

- Request pairing during connection for a Bond positively identified by the
  engine's `wevibe-8bit` profile. This is an IntiHA policy based on observed
  behaviour, not a pairing requirement claimed by the Buttplug specification.
- Learn and persist a pairing-required flag after explicit ATT insufficient
  encryption/authentication errors, including wrapped errors. Do not mistake
  HA/ESPHome API login failures or generic HCI peer termination for ATT errors.
- Pass `pair=True` to the selected backend before protected driver operations.
  Pairing failure or an unsupported backend keeps controls disabled.
- Make one backend connection call per engine-owned attempt. The helper's
  `max_attempts=1` does not cap its separate nine-transient-error budget;
  connection failures now return to the engine's existing backoff loop.
- Preserve bounded cleanup, connection-ID isolation, actual stop verification
  and the prohibition on replaying failed movement commands. Do not add new
  device packets, hard-code GATT handles, delete bonds or copy encryption keys.
- Add `ble_pairing_required` to Status attributes and diagnostics. This is a
  policy flag, not proof of active link encryption.
- Allow HACS-only releases to have a different version from the unchanged
  engine; validate Cargo/app version consistency and list both in release notes.
- Add tests for pairing, cancellation, failed stops, no replay, transient retry
  budgeting, ATT error classification and security-policy persistence.


## 0.2.6

Stability rollback for the HACS integration after users reported unavailable
entities, rapid bridge-session cycling and Bluetooth connection timeouts on
0.2.5. This release does **not** claim to fix the remaining Bond GATT errors.

- Disable v0.2.5's automatic bus-wide active Bluetooth scans on engine hello,
  when enabling IntiHA, and in the 30-second timer. These could overlap
  active proxy GATT connections and initialisation.
- Restore passive/default Home Assistant advertisement-based automatic
  discovery and reconnect behaviour from 0.2.4.
- Keep explicit Search's on-demand active scan, while preventing overlapping
  manual sweeps and suppressing late scans after an engine session changes.
- Leave the Rust engine and BLE protocol unchanged. An installed v0.2.4/0.2.5
  Engine app is compatible with the 0.2.6 HACS integration.
- IMPORTANT: Greyed-out master controls mean the HA config entry is not
  loaded or the frontend/Core is unavailable, rather than just a Bond GATT
  failure. If that persists after a Core restart, inspect Home Assistant
  Core setup errors; engine logs alone do not establish why.

## 0.2.5

Home Assistant Bluetooth discovery hotfix. The HACS integration is the only
runtime component changed; the compatible Engine may remain on 0.2.4.

- Search devices now requests a real Home Assistant on-demand active scan,
  rather than only resending cached Bluetooth advertisements. This includes
  ESPHome scanners left in Auto (passive) mode. The scan runs in the
  background so the Search button does not block until it finishes.
- Request one active sweep when IntiHA first connects or is re-enabled, and
  refresh periodically at a bounded interval (every three minutes) while on.
  Normal listening remains managed by Home Assistant; no separate BLE scanner.
- Forward the updated advertisements after the sweep completes so newly
  identifiable compatible devices can enter Buttplug's normal connection flow.
- Keep GATT connections, stop verification, connection retries and disabled
  controls unchanged. This does not claim to resolve the Bond's peer-terminated
  connection: discovery and successful GATT setup are different stages.
- Include active-scan regression checks; no physical hardware verification.

## 0.2.4

Connection and stop-preparation revision. Update the HACS integration and
Engine app together; restart Home Assistant after the HACS update.

- Separate stop-packet generation from Bluetooth delivery. While a newly
  identified device is not ready, capture the official driver's stop writes
  without starting GATT operations from that driver's queue. This also uses
  the real driver for non-classic profiles rather than applying the classic
  We-Vibe packet to every We-Vibe model.
- Add one atomic `prepare_stop` operation: validate and cache the packet plan,
  execute the real GATT writes, then acknowledge the matching connection ID
  and write count. Only that acknowledgement permits control. A timeout,
  cancellation, disconnect or incomplete acknowledgement keeps control blocked.
- Keep the classic We-Vibe fixed stop behind its existing protocol/endpoint
  checks, but run it through the same atomic transport verification.
- Serialise GATT requests per connection and prevent overlapping engine
  connection attempts. Use one backend connect attempt per engine retry.
- Allow the engine RPC deadline to include HA's bounded failure cleanup;
  keep GATT I/O deadlines unchanged. Do not count teardown as pending GATT I/O.
- Ignore disconnect callbacks from retired client objects, even when a
  connection attempt shares its logical ID with a later client object.
- Show connection stages and retain useful errors. Distinguish link loss,
  stop-packet generation, stop-write failure and bridge-session loss. Replace
  empty exception messages with operation-specific descriptions.
- Add transport regression tests and official-engine Bond test cases. Local
  validation: 45 isolated Python tests passed using fake radio/HA imports.
  The Rust tests, complete HA suite and Docker build were not run locally;
  publication remains gated by the existing GitHub Actions validation.
- Preserve tokenless setup, automatic discovery, cached emergency stops,
  generation fences and the rule against replaying movement after reconnect.

## 0.2.3

Device safe-stop compatibility and diagnostic hotfix.

- Avoid rejecting recognised classic We-Vibe devices merely because Buttplug
  suppresses a redundant stop command while all motor outputs are already zero.
  If the upstream stop produces no GATT writes, send the documented 8-byte
  motors-off packet to the verified We-Vibe control characteristic, wait for
  the GATT write result, and cache it for subsequent emergency stops.
- Keep the refusal and disconnect behaviour for devices without a verified
  protocol-specific stop, failed writes, and lost Bluetooth connections.
- Expose the actual stage of safe-stop failure rather than a generic message.
- No changes to setup, tokens, discovery, or ESPHome proxy configuration.

## 0.2.2

Engine installation hotfix.

- Fix the recurring `"/licenses": not found` Docker error. The image now
  collects pinned Rust dependency licence files from the Cargo registry
  sources inside the builder stage, so Home Assistant does not need an
  `engine/licenses/` directory in the published app repository.
- Extend CI to verify the resulting image actually contains the Buttplug
  dependency licence notices, in addition to checking that the app starts.
- Keep HACS-visible version history and GitHub release notes current.
- No changes to device discovery, Bluetooth behaviour, settings or tokens.

## 0.2.1

App installation and release visibility maintenance release.

- Fix the Supervisor/Docker installation failure caused by the missing
  `engine/licenses/` directory in the published GitHub app build context.
  Supply the complete pinned dependency-licence notices with the app source.
- Add an explicit version-history table to the README displayed by HACS.
- Generate GitHub release descriptions from these changelog entries so HACS
  can show the relevant update notes, once the draft release is published.
- Check the required licence files are included before CI builds the app.
- No changes to device discovery, Bluetooth connections or configuration.

## 0.2.0

Plug-and-play setup and automatic compatible-device connection.

- Remove IntiHA bridge/client tokens; ordinary host/port clients connect directly.
- Discover the HA engine app through Supervisor with one empty confirmation.
  Manual fallback accepts a host/IP or full bridge URL.
- Automatically discover and connect supported BLE devices while enabled,
  with no approval list, MAC address entry or required Search step.
- Start enabled on a new installation and preserve the saved master choice.
- Add all device sensors, stop controls and supported sliders dynamically,
  retaining stable identities and user-assigned names.
- Remove old credentials/selection during configuration migration; keep
  existing useful options and remembered devices.
- Preserve cached stops, exclusive controller ownership, connection-generation
  checks and stopped reconnection. Reset retry backoff after successful readiness.
- Fix discovery during battery polling and commands waiting across a bridge
  reconnect, with regression tests.
- Simplify app/Compose setup and validate startup/discovery with a simulated
  Supervisor, plus automatic device arrival through the real engine/HA stack.

## 0.1.0

Initial implementation for physical hardware evaluation.

- HACS integration with offline-capable setup, options, explicit device
  approval, master switch, search, reconnect, sensors and vibration controls.
- HA Engine app using the pinned official Rust Buttplug server and protocol
  profiles, supporting authenticated clients and active HA Bluetooth paths.
- Bounded requests, separate client/bridge credentials, single-controller
  ownership, generation isolation, stop-plan fallback and no command replay.
- Automated HA, BLE-transport, Rust and cross-process tests; CI metadata and
  app-image checks; physical acceptance procedure.
