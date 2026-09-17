//! The GUI's own settings, kept next to the user's other app data.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
#[serde(rename_all = "camelCase", default)]
pub struct GuiConfig {
    pub cli_path: Option<String>,
    pub last_output: Option<String>,
    pub last_pid: Option<u32>,
}

pub fn config_path() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    base.join("evmedia-gui").join("config.json")
}

/// A missing or unreadable config is not an error — it just means "no preferences yet".
pub fn load() -> GuiConfig {
    std::fs::read(config_path())
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

pub fn save(config: &GuiConfig) -> Result<(), String> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let bytes = serde_json::to_vec_pretty(config).map_err(|error| error.to_string())?;
    std::fs::write(&path, bytes).map_err(|error| error.to_string())
}
