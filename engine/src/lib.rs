//! IntiHA's official Buttplug engine and Home Assistant hardware manager.
pub mod bridge;
pub mod hardware;
pub mod server;

/// Public IntiHA release version reported to Home Assistant and clients.
///
/// Home Assistant releases use the existing `1.0.1` naming convention,
/// while Cargo itself requires a SemVer-valid package version (`0.3.0-b20`).
/// Keep these intentionally separate.
pub const VERSION: &str = "1.0.1";

/// Cargo package version, retained for diagnostics/build provenance.
pub const CARGO_PACKAGE_VERSION: &str = env!("CARGO_PKG_VERSION");
