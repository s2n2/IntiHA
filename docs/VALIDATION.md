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
