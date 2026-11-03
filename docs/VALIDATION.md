# Validation record: IntiHA 0.3.0b1

Build date: 3 November 2026. This record distinguishes executed local checks
from CI jobs that have only been supplied, and from physical tests not performed.

## Executed locally

The Python core/protocol and isolated proxy tests are run using Python 3.13.5,
pytest and aiohttp. The production client and controller communicate through
real localhost WebSocket sockets with a protocol test server. Its device/radio
behaviour is simulated. Proxy tests use fake backend clients. These tests do
not import a pretend Home Assistant package and do not count as real HA tests.

Final measured test totals and packaging checks are recorded in BUILD-REPORT.json.
The suite covers message correlation, heartbeat, stale replies, bad frames,
bounded request waits, disconnect/cancellation cleanup, stop-command fencing,
no replay, dynamic devices, concurrent readiness, output timer independence,
identity ambiguity, battery parsing, source-pinned proxy tests, pairing failure,
service discovery, cleanup failures and package metadata.

## Supplied but NOT executed locally

- Home Assistant 2026.10.4 runtime tests in `tests_ha/` for loading, platform
  entities, config flow and migration. The locally installed Python is older
  than that HA runtime requires.
- Ruff and hassfest checks.
- Docker compilation of unmodified upstream Intiface Engine 4.1.0 on amd64/aarch64.
- Protocol smoke test against that actual compiled upstream binary.
- HA OS native-app installation, D-Bus permissions and Supervisor discovery.

The corresponding CI jobs are release gates, not evidence of a successful
remote run. No Rust toolchain, Docker daemon, live HA instance or internet
package-download access was available in the authoring container.

## Physical tests NOT performed

No physical Bond, garage ESP32 D1 Mini, C3, USB adapter, BLE encryption exchange,
protected motor write, hardware stop, proxy firmware comparison, concurrent
physical devices or room-to-room reconnection was exercised. The garage proxy's
actual ESPHome/ESP-IDF versions remain unverified.

This release has no production HA-proxy motor-control backend. The diagnostic
explicitly returns `protected_motor_write_verified: false` and never calls a
GATT write. A connection/pairing/services pass is not a Bond control pass.

## Commands

```bash
python -m pip install -r requirements-dev.txt
python tools/check_repository.py
python -m pytest tests
ruff check .
```

In a separate Python >=3.14.2 environment:

```bash
python -m pip install -r requirements-ha-test.txt
python -m pytest tests_ha
```

The GitHub native-image job compiles the real binary and then runs
`tools/smoke_native_engine.py` against an EMPTY hardware-disabled server. The
smoke test refuses device commands if inventory is nonempty.
