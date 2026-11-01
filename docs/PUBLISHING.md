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
release. When ready to prepare version 0.2.0:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The release workflow validates version consistency and prepares a **draft**
release with the HACS archive after all checks pass. Review and publish the
draft when hardware testing supports the release description. This project
does not claim inclusion in HACS's default catalogue.
