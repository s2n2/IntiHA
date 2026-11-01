# First hardware acceptance test

Software tests cannot establish radio performance or every vendor firmware
behaviour. Run these checks before depending on IntiHA's automatic recovery.
Keep the device's physical stop control within reach during testing.

## Record the environment

Record HA version, IntiHA version, engine version, device make/model, active
proxy model/firmware and network type. Start with one device and one active
proxy, then add more devices and proxies after the single-device test passes.
Do not include secret tokens or personal activity data in a test report.

## Pass criteria

| Test | Expected result |
|---|---|
| HA starts while the engine is stopped | IntiHA entry loads; master switch and connection diagnostics remain available. |
| Engine starts later | Authenticated bridge connects automatically; no output starts by itself. |
| Search finds an unapproved device | It appears as a candidate without a Bluetooth connection or actuation. |
| Approve and connect | The engine recognises it, stores its stop plan, creates supported controls and leaves output stopped. |
| Classic We-Vibe first connection | No initialisation pulse; test the actual model explicitly. |
| Each HA vibration slider at a low level | Correct motor responds; slider value is labelled as a command, not sensor feedback. |
| Device stop and Stop all | Reachable devices stop promptly. |
| Manual command timeout | The device stops after the configured interval without further movement commands. |
| External client connects | Correct protocol handshake and device list; HA motion controls unavailable; HA stop remains available. |
| External client exits or loses connection | Stop attempted; ownership released; values cleared. |
| Power cycle the device | Offline reported, automatic reconnect when advertising again, no old output replay. |
| Move from one active proxy area to another | Offline interval is visible; a fresh connection recovers through available coverage; prior output is not resumed. |
| Turn master off during connection | Pending connection closes; no late reconnection or movement. |
| Stop engine while output is running | HA attempts its cached stop writes and disconnects. Record actual outcome and delay. |
| Disconnect proxy network while output is running | Offline/uncertain status; no false physical-stop claim. Recover only to a fresh stopped connection. |
| Restart HA | Existing sessions end; stored enable preference returns but no output level is restored. |
| Shelly-only coverage | Advertisements may appear, but the device cannot become controllable without a GATT-capable path. |
| Exhaust proxy slots | Clear offline/error state and bounded reconnect; other HA devices remain usable. |
| Two external clients | The second is rejected; no competing actuator owner. |

## Room coverage log

Use a simple table while carrying a single device between rooms. Record the
room, active proxy, disconnection duration, time to recover, and whether the
device returned stopped. Add or relocate active proxies where links fail
frequently. A strong advertisement from a Shelly is not evidence of an active
control path.

Do not treat a finite passing test as proof that a Bluetooth or Wi-Fi failure
can never happen. If a stop cannot be delivered, use the physical control and
record the failure before continuing.
