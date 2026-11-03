# Changelog

## 0.3.2

- Fix release versioning for Cargo: use valid SemVer `0.3.2` in both `Cargo.toml` and `Cargo.lock`.
- Keep the Supervisor slug `intiha_engine` so existing 0.2.4 installations upgrade in place.
- Remove stale guidance that incorrectly told users to use the experimental `intiha_proxy_engine` slug.
- The obsolete 0.3.0b3 proxy app used invalid Rust version `0.3.0b3` and must be uninstalled if still present.

## 0.3.1

- Experimental note: 0.3.1 packaging briefly used a separate proxy-engine slug; 0.3.2 supersedes that approach and restores `intiha_engine` for in-place upgrades.
- Add an explicit startup log marker showing `transport=home_assistant_proxy` and `local_bluetooth=false`.
- Keep the Home Assistant / ESPHome-proxy transport only; no local Bluetooth adapter, BlueZ, D-Bus or btleplug manager is used.
- Retain the 0.3.0b2 proxy transport and reconstructed 0.2.7 connection/security fixes.

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

## 0.3.1

- Proxy-native engine replaces the existing `intiha_engine` app in-place.
- Restores the original Supervisor slug so installed 0.2.4 systems receive an upgrade rather than a second app.
- No local Bluetooth adapter is required; all BLE transport remains through Home Assistant / ESPHome Bluetooth proxies.
