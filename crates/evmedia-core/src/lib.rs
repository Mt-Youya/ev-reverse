//! Portable core: catalog, download, decode, crypto and the harvest orchestration loop.
//!
//! This crate deliberately makes no Windows API call. Live process reading and playhead
//! control live in `evmedia-win` and reach the loop here through the `harvest::Harvester`
//! seam, which is what lets the harvest loop be tested without a player running.

pub mod catalog;
pub mod crypto;
pub mod decode;
pub mod download;
pub mod harvest;
pub mod keyscan;
pub mod media;
pub mod paths;

use anyhow::{Context, Result};
use std::path::Path;

/// Read one of the documented JSON contracts, naming the file in any error.
pub fn read_json<T: for<'de> serde::Deserialize<'de>>(path: &Path) -> Result<T> {
    serde_json::from_slice(&std::fs::read(path).with_context(|| format!("read {}", path.display()))?)
        .with_context(|| format!("parse {}", path.display()))
}
