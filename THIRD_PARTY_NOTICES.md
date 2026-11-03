# Third-party notices

This repository's new Python client and packaging are covered by LICENSE.
No upstream engine or Bluetooth source is vendored in this source archive.

The native app installs the unmodified published `intiface-engine` 4.1.0 and its
Cargo.lock-selected dependencies from the Cargo registry. Their individual
licences apply. The image preserves available dependency LICENSE/COPYING/NOTICE
files in `/usr/share/licenses/intiface/`. The app is a distributor/packager,
not a claim to own or independently implement those protocols.

HA, aiohttp, ESPHome, bleak-esphome, habluetooth and related runtime components
remain their respective upstream projects. Diagnostic use of HA's registered
connector does not copy their implementation or extract/share Bluetooth keys.
