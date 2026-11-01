# Put this project in s2n2/IntiHA

The project contains both parts needed for installation:

- `custom_components/intiha/` for HACS;
- `engine/` and `repository.yaml` for the HA App store.

Copy the **contents** of the supplied `IntiHA/` directory into the root of the
GitHub repository. Do not put the entire directory inside another `IntiHA/`
folder. Keep `.github`, `.gitignore` and `.gitattributes` as well as the visible
folders. IntiHA starts with default settings; no client or bridge credentials are required.

## With an authenticated Git checkout

If you have Git installed and authenticated on your computer:

```bash
git clone https://github.com/s2n2/IntiHA.git
```

Copy the supplied project files into that checkout, then run inside it:

```bash
git add .
git commit -m "Add IntiHA integration and engine app"
git push -u origin main
```

If your empty clone started on a branch named `master`, run
`git branch -M main` before the push. Use your normal GitHub authentication;
do not put a personal access token in the remote URL or a committed file.

## Before installing

Open the repository's **Actions** tab and check the validation run. The workflow
runs HACS/hassfest validation, Python tests, Rust tests, the combined system
test and native amd64/aarch64 container builds. The container builds are
especially useful because a Docker runtime was not available in the original
development workspace.

Set the repository's **About** description to “Buttplug server using Home
Assistant Bluetooth proxies” and add at least one relevant topic, such as
`home-assistant`, `hacs`, or `bluetooth`. The HACS validation action checks this
GitHub repository metadata as well as the files.

Once that run passes, follow the installation steps in the root README. The
same GitHub repository URL is used in HACS and in the HA App store.

## Optional release tag

The default branch works as a HACS custom repository without a published
release. When ready to prepare version 0.2.2:

```bash
git tag v0.2.2
git push origin v0.2.2
```

The release workflow validates version consistency and prepares a **draft**
release with the HACS archive after all checks pass. Review and publish the
draft when hardware testing supports the release description. This project
does not claim inclusion in HACS's default catalogue.

## Release notes shown in HACS

HACS displays the repository README as the information page. Keep the
**Version history** section of the README and `CHANGELOG.md` updated together.
The release workflow extracts the matching version's section from `CHANGELOG.md`
and creates a **draft** GitHub release. **Publish the draft** in GitHub's Releases
tab when the build is validated. HACS updates then expose that published
release's notes. Merely pushing a tag or leaving the release in draft does not
make its update notes visible.

For version 0.2.2, the app no longer requires `engine/licenses/` in GitHub.
The Dockerfile collects the relevant licence files from Cargo at build time.
Wait for the GitHub Actions app-image check to pass before publishing the tag.
The HA Supervisor Docker build uses `engine/` as its build context and requires
the bundled dependency-licence files.
