# Publishing

This source was packaged locally, not pushed to GitHub. Use a clean Git branch
and the supplied patch, or inspect the complete repository snapshot and deletion
manifest. Never claim a remote release or container image has been published
until GitHub confirms it.

## Gates

`Validate rebuild` runs core/protocol tests, Ruff, a real HA 2026.10.4 loader test,
hassfest, and builds the actual pinned upstream engine on amd64 and aarch64.
The image test runs the actual native binary without hardware managers and
checks the real v3 handshake/inventory/ping/stop messages against the production
client. This is not a Bluetooth or HA OS installation test.

Home Assistant 2026.10.4 requires Python >=3.14.2; the HA CI job uses Python 3.14.
Core tests also ran locally on Python 3.13 because they deliberately do not
import HA. CI requirements must not be confused with local validation results.

After reviewing green CI:

```bash
git tag v0.3.0b1
git push origin v0.3.0b1
```

The release workflow runs all validation gates again before creating a **draft
prerelease** using the current changelog section. Inspect its artifacts and then
publish the prerelease. HACS users may need to enable beta releases/select this
version. `hacs.json` renders README, which includes the complete version-history
table and prominently states the missing proxy-control scope.

No engine image is pushed to a container registry. The app builds its local
Dockerfile when installed. For a later production release, publish verified
multi-architecture images rather than imposing Rust builds on every HA host.
This preview does not silently fetch an unverified prebuilt binary.
