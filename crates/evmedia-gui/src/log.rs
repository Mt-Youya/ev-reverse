//! A log file for the window, because a GUI has nowhere to print.
//!
//! The first-run flow asks three questions — is there a player, is Frida reachable, does the probe
//! answer — and when one of them says no, the user has only a tooltip to go on. This is where the
//! answers go, next to the config, so a failure can be read instead of guessed at.

use std::{
    fs::OpenOptions,
    io::Write,
    path::PathBuf,
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};

static PATH: OnceLock<PathBuf> = OnceLock::new();

/// Where the log goes. Next to the config, so "open %APPDATA%\evmedia-gui" finds everything.
pub fn path() -> PathBuf {
    PATH.get()
        .cloned()
        .unwrap_or_else(|| crate::config::config_path().with_file_name("app.log"))
}

pub fn info(message: &str) {
    write("INFO", message);
}

/// Something failed but the run continues.
pub fn warn(message: &str) {
    write("WARN", message);
}

fn write(level: &str, message: &str) {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or_default();
    let path = path();
    let _ = PATH.set(path.clone());
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(file, "[{stamp}] {level} {message}");
    }
}

/// Trim the log so it cannot grow without bound across months of use.
pub fn rotate_if_large() {
    let path = path();
    if std::fs::metadata(&path).map(|meta| meta.len() > 1_000_000).unwrap_or(false) {
        let _ = std::fs::remove_file(&path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_log_lives_next_to_the_config() {
        assert_eq!(path().file_name().unwrap(), "app.log");
        assert_eq!(path().parent(), crate::config::config_path().parent());
    }
}
