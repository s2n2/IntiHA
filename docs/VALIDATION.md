# 0.2.7 validation record

Date: 1 November 2026. Integration version: 0.2.7. Engine unchanged at 0.2.5;
Engine 0.2.4 is also bridge-compatible.

**Executed:** 84 isolated tests passed with Python 3.13.5, pytest,
pytest-asyncio and aiohttp. Production `ble.py`, `bridge.py` and `security.py`
run against test doubles for HA imports and the BLE radio. Five tests execute
selected production coordinator methods via their AST for security-policy
persistence. Reproduce using `python tools/run_offline_transport_tests.py`.

Coverage added: requested pairing, unsupported/failed pairing, cancellation
cleanup, ATT error recognition, policy persistence, no movement replay,
stop verification after a fresh paired connection, and suppression of a
modelled nine-attempt transient retry loop. The real upstream dependency and
radio stacks were NOT executed by these isolated tests.

**Additional checks:** changed Python files compile; changed JSON/YAML parse;
release archive/notes generation succeeds with a 0.2.7 integration and 0.2.5
engine; Git patch application and byte-for-byte output checks pass.

**Not executed:** full Home Assistant test runtime (including the three added
normal coordinator tests), Ruff, hassfest, HACS remote validation, Rust or
Docker. Those tools/dependencies are unavailable locally. The existing
config-flow file requires the project's Python 3.14 runtime, so whole-package
compilation is not claimed from the local Python 3.13 runtime. Run the normal
GitHub CI gates before publishing.

**Physical status:** no access to the user's Bond, ESPHome radio or HA system.
This patch addresses confirmed code omissions; it is not a claim of a verified
hardware fix or seamless encrypted roaming. See
[the specification review](BLUETOOTH_SECURITY_REVIEW.md).

The records below are historical and were not rerun as full-system tests.

---

# 0.2.4 validation record

Date: 1 November 2026.

**Executed for this patch:** 45 isolated Python transport tests passed using
Python 3.13.5, pytest, pytest-asyncio and aiohttp. The production `ble.py` and
`bridge.py` are exercised, with Home Assistant import surfaces and the radio
replaced by test doubles. This is NOT a full Home Assistant or physical BLE
validation. Reproduce with `python tools/run_offline_transport_tests.py` after
installing pytest, pytest-asyncio and aiohttp.

New cases cover successful atomic stop verification/caching, queued-command
serialization, stalled/failed writes, dropped links, cancellation cleanup,
stale IDs, invalid write properties, abandoned-client callbacks and useful
connect/stop timeout messages. Existing bridge lifecycle tests remain included.

Additional checks: changed Python files compile; JSON/YAML/TOML parse; release
versions agree; patch applies to the supplied 0.2.3 baseline. These checks are
reported separately from functional tests.

**Not executed locally for 0.2.4:** Rust compiler/clippy/rustfmt, the new actual
Buttplug Bond tests, the full HA integration suite, hassfest/HACS remote checks,
or Docker builds. The environment lacks Rust, Docker and Home Assistant.
GitHub Actions retains these validation gates. The Bond tests derive GATT
layout from the pinned official device configuration, rather than guessing a
classic profile or using a hand-coded Bond stop packet.

**Physical validation pending:** the user's Bond and ESP32-C3 proxy. A completed
GATT write is a transport acknowledgement, not measured motor feedback; no
software test guarantees a stop after a physical radio link is lost.

The records below belong to earlier versions and were NOT rerun for 0.2.4.

---

## 0.2.2 packaging correction (validation status)

- The image build no longer copies `engine/licenses/` from the Docker context.
- A builder-stage step collects upstream licences from the pinned Cargo packages.
- CI now checks the compiled app image contains three key Buttplug licences.
- Static Dockerfile assertions and a shell fixture of the licence-collection step
  passed during patch preparation. Full Docker image builds were not available
  in this environment and must pass in GitHub Actions / Home Assistant.

# Validation record: IntiHA 0.2.1 (prior tests from 0.2.0)

Date: 1 November 2026.

This record distinguishes software verification from physical Bluetooth and
deployment verification. No physical toys, Shelly devices or ESPHome radios
were accessible in the development environment.

## Environment

| Component | Version |
|---|---|
| Home Assistant | 2026.10.4 |
| Python | 3.14.8 |
| Rust | 1.99.1 |
| buttplug_server | 12.0.2 |
| buttplug_core | 11.0.2 |
| buttplug_server_device_config | 12.0.1 |
| Bleak | 3.0.2 |
| Bleak Retry Connector | 4.7.1 |
| habluetooth | 7.1.2 |

## Executed checks

| Check | Result |
|---|---|
| Complete Python test suite, including mandatory system tests | **108 passed** |
| Rust unit/regression tests | **15 passed** |
| Rust real TCP/WebSocket integration tests | **4 passed** |
| Ruff checks and formatting | Passed |
| Cargo formatting and clippy with warnings denied | Passed |
| Home Assistant 2026.10.4 hassfest | **23 validation plugins; 1 integration; 0 invalid; no warnings** |
| Official HACS manifest schemas | Passed |
| Project JSON/YAML/TOML parsing and version consistency | Passed |
| Actual app entrypoint with a simulated Supervisor | **3 passed** (included in the Python total) |
| Supervisor discovery / simplified configuration flows | **27 passed** (included in the Python total) |
| App startup shell syntax | Passed |

The final Python suite was run with `INTIHA_REQUIRE_SYSTEM_TESTS=1` so a missing
engine executable would fail the gate. Home Assistant's checks for leftover
tasks and timers remained enabled; no cleanup exemptions were used.

Hassfest was pinned to Home Assistant core commit
`357cae5c598dfcd7a0ea7dc5b1facccd4ec6037b`. The HACS checks used the official
manifest, integration-manifest and integration-category schemas. The full HACS
GitHub Action was not run locally: it requires the published repository and
authenticated GitHub metadata access.

## What the combined system tests establish

These tests launch the actual compiled Rust engine as a separate process,
load the real integration and entity platforms in Home Assistant, use the
actual tokenless WebSocket bridge, and simulate only the BLE radio.

- An upstream WeVibe Nova profile is recognised automatically on a fresh
  installation, without tokens, device selection, Search or turning the master
  switch on manually. Two real HA vibration entities are created. A real HA number service call produces the expected
  characteristic writes.
- A second device powered on after startup connects automatically. All its
  sensors, stop button and supported vibration entities appear without a
  reload, while the first device remains connected.
- The classic We-Vibe initialisation pulse is replaced with a stop packet.
  The official driver's stop writes are captured and installed in HA before
  the device is marked ready.
- Killing the engine process triggers HA's cached stop and disconnect path.
  Restarting reconnects with a new connection ID and does not replay output.
- Failed stop writes propagate across the real engine/bridge boundary and
  leave the unconfirmed-stop status visible.
- The actual external Buttplug v3 handshake without credentials, one-controller
  restriction and exclusion of competing HA movement work together.

The Rust integration tests additionally exercise v4 negotiation and verify
that an old HA movement request is rejected after reconnect even when the
public Buttplug device index is reused. Ready-link loss invalidates an
external controller's session before it can target the new connection.

## Failure and cleanup coverage

Tests cover compatible-device recognition, continuous discovery, live entity
creation and deduplication, saved master-off state, per-device serialisation, bounded
requests, cancelled connects, stale BLE notifications/disconnects, writes
queued behind a failure, notification overflow, stop-before-disconnect order,
independent manual timeout, uncertain stops surviving an empty inventory or
restart, and local off/stop enforcement before a stalled engine reply.

WebSocket regressions cover shutdown during a handshake, simultaneous
reconnect/close, closing from a receiver callback and cancellation while
socket cleanup is already underway. These checks found and resolved a real
heartbeat-timer leak before the initial release. The 0.2.0 regressions also
verify that an admin request waiting for the send lock cannot cross a
reconnection, even if the old pending response has already failed. Discovery
during an awaited battery read does not interrupt polling. Successful device
readiness resets reconnect failure backoff; a stale ready result cannot revive
a retired session.

## App discovery validation

The actual shell entrypoint is tested against a simulated Supervisor and
engine process. Checks cover startup without configuration, the app hostname
announcement, retry during Supervisor startup, preservation of engine exit
status, and shutdown of the discovery worker and engine. The built-in
Supervisor credential is not exposed to the engine, command arguments or logs.
HA configuration-flow tests use the real flow manager, including Supervisor
discovery and manual discovery lookup. A live HA OS installation was not
available. The standalone Supervisor app-schema validator was not run
successfully because its isolated environment lacked `aiodocker`.

## Checks still needed after publication

1. **Native container builds.** Docker was unavailable locally. CI is provided
   to build and health-check the app on amd64 and aarch64. Run it before
   installing the initial app release.
2. **Full remote HACS validation.** Publish the source and set the repository's
   description/topics so the HACS action can inspect the real repository.
3. **Physical device and room testing.** Follow
   [HARDWARE_TESTING.md](HARDWARE_TESTING.md) with the actual device models and
   proxy firmware. Software simulation cannot establish radio coverage,
   physical stop behaviour or uninterrupted roaming.

The release is an initial implementation for hardware evaluation. These
results support its software behaviour and failure handling; they are not a
claim of guaranteed physical safety or guaranteed radio reliability.

## 0.2.1 packaging fix

The 0.2.1 patch includes the dependency-licence directory in the repository
patch, adds an early file-presence check before the CI Docker build, and
updates the app/integration/Cargo versions. This does not imply that a physical
HA OS install or Docker image build was executed in this environment.

## 0.2.5 Bluetooth discovery patch (1 November 2026)

- Source-only regression tests for the Home Assistant active-scan lifecycle:
  **4 passed**. These use AST-extracted production coordinator methods and fake
  Bluetooth APIs; they do not represent real radio testing.
- Existing BLE transport tests against production `ble.py` with stubbed
  Home Assistant and Bluetooth backends: **33 passed**.
- Python syntax compilation and Git patch reverse-application checks passed.
- Home Assistant 2026.6 and newer document `bluetooth.async_request_active_scan`,
  so this call is supported by the documented 2026.10 minimum release.
- Rust engine connection logic and stop protocol are unchanged; a 0.2.4
  engine remains bridge-compatible with the 0.2.5 HACS integration.
- NOT RUN: full Home Assistant/hassfest/HACS CI, Rust build/tests, Docker
  installation, or tests with actual Bond and ESPHome hardware.

## 0.2.6 HACS stability hotfix (1 November 2026)

- Removed all automatic active-scan triggers introduced in 0.2.5; retained
  explicit Search and regular advertisement callbacks.
- Added direct coordinator regression checks: manual scan only, no active
  scans on hello/enable/timer, concurrent manual scan suppression, obsolete
  bridge epoch and cancellation handling.
- No Rust or engine protocol modifications. A new engine Docker build is not
  required for the HACS-only update.
- Pending: actual Home Assistant/Core 2026.10.4, full GitHub CI, real ESPHome
  proxies and We-Vibe Bond verification. If HA controls remain unavailable,
  obtain Core traceback; the Engine log cannot identify HA setup failures.
