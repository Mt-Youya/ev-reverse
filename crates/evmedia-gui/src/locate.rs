//! Finding the `evmedia` CLI.
//!
//! A window with no CLI is useless, so a failure here is surfaced in the UI with instructions
//! rather than as an empty window. The resolved path and version stay visible so it is always
//! clear which build is being driven.

use crate::config;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

const EXE: &str = if cfg!(windows) { "evmedia.exe" } else { "evmedia" };

#[derive(Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct CliStatus {
    pub path: Option<String>,
    pub source: String,
    pub version: Option<String>,
    pub ok: bool,
    pub detail: String,
}

/// Search order: an explicit override, the environment, next to this executable, then PATH. In a
/// workspace build all binaries land in the same `target/release`, so step three is what makes a
/// development build work with no configuration at all.
pub fn resolve(override_path: Option<&str>) -> Result<(PathBuf, &'static str), String> {
    if let Some(value) = override_path.filter(|value| !value.trim().is_empty()) {
        let path = PathBuf::from(value);
        if path.is_file() {
            return Ok((path, "手动指定"));
        }
        return Err(format!("指定的 CLI 不存在：{}", path.display()));
    }
    if let Some(value) = std::env::var_os("EVMEDIA_CLI") {
        let path = PathBuf::from(value);
        if path.is_file() {
            return Ok((path, "环境变量 EVMEDIA_CLI"));
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidate = dir.join(EXE);
            if candidate.is_file() {
                return Ok((candidate, "与 evmedia-gui 同目录"));
            }
        }
    }
    if let Some(paths) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&paths) {
            let candidate = dir.join(EXE);
            if candidate.is_file() {
                return Ok((candidate, "PATH"));
            }
        }
    }
    Err(format!(
        "找不到 {EXE}。先在仓库根目录执行 cargo build --release，或在设置里手动指定路径。"
    ))
}

pub fn version_of(cli: &Path) -> Option<String> {
    let output = std::process::Command::new(cli).arg("--version").output().ok()?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

#[tauri::command]
pub fn check_cli(override_path: Option<String>) -> CliStatus {
    let override_path = override_path.or_else(|| config::load().cli_path);
    match resolve(override_path.as_deref()) {
        Ok((path, source)) => {
            let version = version_of(&path);
            CliStatus {
                ok: version.is_some(),
                detail: match version {
                    Some(_) => String::new(),
                    None => format!("{} 存在但无法执行", path.display()),
                },
                path: Some(path.display().to_string()),
                source: source.to_string(),
                version,
            }
        }
        Err(error) => CliStatus {
            path: None,
            source: String::new(),
            version: None,
            ok: false,
            detail: error,
        },
    }
}

#[tauri::command]
pub fn pick_directory(current: Option<String>) -> Option<String> {
    let mut dialog = rfd::FileDialog::new();
    if let Some(dir) = current {
        dialog = dialog.set_directory(dir);
    }
    dialog.pick_folder().map(|path| path.display().to_string())
}

/// Pick a file, optionally restricted to one extension. The media binaries are "a file the user
/// has somewhere", so this is the picker for them.
#[tauri::command]
pub fn pick_file(current: Option<String>, filter: Option<String>) -> Option<String> {
    let mut dialog = rfd::FileDialog::new();
    if let Some(dir) = current {
        if let Some(parent) = Path::new(&dir).parent() {
            dialog = dialog.set_directory(parent);
        }
    }
    if let Some(extension) = filter.filter(|value| !value.trim().is_empty()) {
        dialog = dialog.add_filter(extension.to_uppercase(), &[extension.as_str()]);
    }
    dialog.pick_file().map(|path| path.display().to_string())
}
