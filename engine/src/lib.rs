//! IntiHA's official Buttplug engine and Home Assistant hardware manager.
pub mod bridge;
pub mod hardware;
pub mod server;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
