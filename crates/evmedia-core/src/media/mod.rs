//! Turning decrypted segments into one playable file: concatenate, then remux.

pub mod mux;
pub mod remux;

pub use mux::{merge_lesson, MergeOutcome};
pub use remux::{remux_mp4, RemuxOutcome};
