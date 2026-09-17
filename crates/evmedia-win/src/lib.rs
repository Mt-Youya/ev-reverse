//! Windows live source: read the running EVPlayer2's memory, derive segment keys, and drive
//! its playhead.
//!
//! The whole crate is gated off on non-Windows, and `windows-sys` is declared under
//! `[target.'cfg(windows)'.dependencies]`, so on Linux/macOS this compiles to an empty crate
//! and `cargo check --workspace` still succeeds. Because the gate is at the crate root, no
//! module below needs its own `#[cfg]`.
#![cfg(windows)]

pub mod capture;
pub mod playhead;
pub mod process;
pub mod scan;
pub mod source;
pub mod sweep;

pub use process::find_player_pid;
pub use source::WinSource;
