//! Path hygiene for manifests that arrive from outside this program.

use anyhow::{bail, Result};
use std::path::{Component, Path, PathBuf};

/// Reject anything that could escape the output directory when joined to it.
pub fn safe_relative(path: &str) -> Result<PathBuf> {
    let value = Path::new(path);
    if value.is_absolute()
        || value
            .components()
            .any(|part| matches!(part, Component::ParentDir | Component::Prefix(_)))
    {
        bail!("unsafe relative path: {path}");
    }
    Ok(value.to_path_buf())
}
