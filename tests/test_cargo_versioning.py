from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cargo_package_version_is_valid_semver_prerelease() -> None:
    cargo = tomllib.loads((ROOT / "engine" / "Cargo.toml").read_text())
    version = cargo["package"]["version"]
    assert version == "0.3.0-b8"
    assert re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version)


def test_public_engine_version_stays_home_assistant_release_version() -> None:
    lib = (ROOT / "engine" / "src" / "lib.rs").read_text()
    assert 'pub const VERSION: &str = "0.3.0b8";' in lib
    assert 'env!("CARGO_PKG_VERSION")' in lib


def test_lockfile_root_package_matches_cargo_version() -> None:
    lock = (ROOT / "engine" / "Cargo.lock").read_text()
    assert 'name = "intiha-engine"\nversion = "0.3.0-b8"' in lock
