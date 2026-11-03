# IntiHA 0.3.2 upgrade

The 0.3.0b3 experimental proxy app cannot build because `0.3.0b3` is not valid Cargo SemVer. It also used the temporary Supervisor slug `intiha_proxy_engine`.

1. If **IntiHA Proxy Engine** / slug `intiha_proxy_engine` is installed, stop and uninstall it.
2. Replace the GitHub repository contents with this 0.3.2 release and push them.
3. In Home Assistant, refresh the App Store repository.
4. Your existing **IntiHA Engine** / slug `intiha_engine` should now offer version **0.3.2** as an upgrade from 0.2.4.
5. Update the HACS integration to 0.3.2 and restart Home Assistant.
6. Update/install the Engine app and start it.

Expected app metadata:
- slug: `intiha_engine`
- version: `0.3.2`
- Cargo package version: `0.3.2`
- Bluetooth transport: Home Assistant / ESPHome proxies
- local Bluetooth adapter: not required
