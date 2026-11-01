"""Prevent the published HA app requiring an untracked host-side directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_license_build_is_self_contained():
    dockerfile = (ROOT / "engine" / "Dockerfile").read_text()
    assert "COPY licenses " not in dockerfile
    assert "registry/src" in dockerfile
    assert "COPY --from=builder /build/dependency-licenses/" in dockerfile
    assert 'RUN cargo build --release --locked --bin intiha-engine' in dockerfile
