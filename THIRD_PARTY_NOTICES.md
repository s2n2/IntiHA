# Third-party notices

IntiHA uses the official Buttplug libraries without vendoring or renaming their device protocol implementations:

| Dependency | Pinned version | Licence |
|---|---|---|
| buttplug_server | 12.0.2 | BSD-3-Clause |
| buttplug_core | 11.0.2 | BSD-3-Clause |
| buttplug_server_device_config | 12.0.1 | BSD-3-Clause |

Buttplug is copyright Nonpolynomial Labs, LLC and contributors. The original
copyright notices and licence terms are collected directly from the pinned
Rust dependency sources while the app image is built (see
[engine/Cargo.lock](engine/Cargo.lock)). The image installs these files under
`/usr/share/licenses/intiha/dependencies/`. This avoids requiring the large
`engine/licenses/` source directory in the Home Assistant app repository.

Home Assistant, Bleak, Bleak Retry Connector and the HA Bluetooth stack are
separate runtime dependencies supplied by Home Assistant; their code is not
copied into the HACS integration. Refer to those projects for their licences.

The IntiHA name does not imply endorsement by Home Assistant, ESPHome, Shelly,
Intiface, Buttplug, We-Vibe, or another device manufacturer.
