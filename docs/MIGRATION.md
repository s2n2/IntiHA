# Migration from IntiHA 0.2.x

## Before changing files

1. Take a Home Assistant backup and record the prior HACS release and app version.
2. Turn the old IntiHA integration off. Stop its old `intiha_engine` app and
   disable Start on boot/Watchdog for that app. Do not run competing device controllers.
3. Preserve the old installation/backup for rollback. The new app is a different
   slug and data directory; it cannot silently overwrite the old app's settings.

## Repository update

The Git patch is against the supplied 0.2.7 reconstructed source, not against
an independently fetched live GitHub branch. Start on a clean branch:

```bash
git switch -c native-rebuild
git apply --check /path/to/IntiHA-v0.3.0b1.patch
git apply /path/to/IntiHA-v0.3.0b1.patch
git add -A
git commit -m "Rebuild IntiHA around native upstream Intiface"
git push -u origin native-rebuild
```

Do not force a failed patch application. The complete source ZIP is an
alternative repository snapshot. It includes a deletion manifest for the old
files; **overlaying files alone does not delete the obsolete engine/** directory.
For a clean checkout, use the patch or replace the repository's tracked contents
from the snapshot, inspect `git diff`, and retain your `.git` directory.

## Home Assistant integration

After CI passes and the preview release is published, select 0.3.0b1 in HACS
and restart Home Assistant. Config-entry migration intentionally disables control
and marks the entry `migration_required` while keeping master controls loaded.

Open Configure. Choose a standard native Intiface WebSocket endpoint:

- Existing phone/computer: `ws://HOST:12345` (use that server's actual port).
- New native HA app: `ws://HOME_ASSISTANT_IP:12347`.

The old port 12346 `/bridge` is NOT a client endpoint and is rejected. The old
custom engine's port 12345 is also not the new native baseline: stop that app.

Once the address is saved, turn Enabled on. New device entities use server-based
identity instead of the old MAC-based identity. Old entity records may remain
unavailable until removed through HA's UI; existing automations using old device
entities must be updated. Do not delete old records before preserving the backup.
Hub controls reuse names/IDs where possible; this is not a promise of complete
entity-registry continuity. Movement levels are never migrated.

## Native app

Install **IntiHA Native Engine**, not the old **IntiHA Engine**. It uses host
D-Bus and a real local controller. No local Bluetooth hardware? Use the existing
phone/computer server instead. The garage D1 Mini remains a remote diagnostic
candidate, not a native adapter.

## Rollback

Use the Home Assistant backup taken before migration. The new config-entry
version is 3 and old code cannot be assumed to understand it, so simply copying
0.2 files back over the top is not a safe rollback procedure. Stop the new app
before restoring the old one. Never reactivate both against one peripheral.
