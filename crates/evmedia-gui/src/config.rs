//! The window's settings, kept next to the user's other app data.
//!
//! These are the answers to the questions the window asks before it can plan anything: which CLI
//! to drive, whose session to use, where to write, and how much of the machine to use doing it.

use crate::plan::Options;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Segment-level parallelism inside one video. The CLI's own default; changing it changes how hard
/// one lesson pushes the network.
pub const DEFAULT_JOBS: usize = 8;
/// Video-level parallelism. A lesson can enter a CPU-heavy EVC compatibility conversion after its
/// download completes, so one active lesson is the responsive default for a desktop machine.
pub const DEFAULT_WORKERS: usize = 1;

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
#[serde(rename_all = "camelCase", default)]
pub struct GuiConfig {
    pub cli_path: Option<String>,
    /// The session file. Empty means "the one a sniff writes beside the export root", which is what
    /// `session_path` resolves — the window never makes the user find this.
    pub session: String,
    pub account: i64,
    /// Everything the app writes goes below this directory.
    pub root: String,
    pub workers: usize,
    pub jobs: usize,
    /// `mp4` or `mkv`.
    pub extension: String,
    pub ffmpeg: String,
    pub ffprobe: String,
    /// Re-export a video whose output already exists instead of skipping it.
    pub force: bool,
    /// Remembered so a restart lands on the same screen.
    pub last_selected: Vec<String>,
}

impl GuiConfig {
    /// Apply the defaults a fresh install should start from, leaving anything the user has set.
    ///
    /// The export root is resolved to an absolute path here rather than at each use, because every
    /// consumer needs the absolute one and three separate places (the session path, the planned
    /// output, the child process's working directory) silently disagree when it is not. A `\\?\`
    /// verbatim path is avoided on purpose: it is correct for the filesystem and unreadable in every
    /// log line and argv preview.
    pub fn with_defaults(mut self) -> Self {
        if self.workers == 0 {
            self.workers = DEFAULT_WORKERS;
        }
        if self.jobs == 0 {
            self.jobs = DEFAULT_JOBS;
        }
        if self.extension.is_empty() {
            self.extension = "mp4".to_string();
        }
        if self.ffmpeg.is_empty() {
            self.ffmpeg = "ffmpeg".to_string();
        }
        if self.ffprobe.is_empty() {
            self.ffprobe = "ffprobe".to_string();
        }
        if !self.root.trim().is_empty() {
            self.root = absolute(&self.root)
                .map(|path| path.display().to_string())
                .unwrap_or_else(|_| self.root.trim().to_string());
        }
        self
    }

    /// The same settings with the absolute root applied.
    ///
    /// The frontend sends whatever is in its text boxes, so every command that derives a path from
    /// the root has to normalise it first — otherwise the session lands in one place and is looked
    /// for in another, which is exactly the bug a relative root produced.
    pub fn normalized(&self) -> Self {
        self.clone().with_defaults()
    }

    /// Where the session file is, defaulting to the one a sniff writes next to the export root. The
    /// window never asks the user to find this path: it either exists where the app put it, or the
    /// app can read it out of the running player.
    pub fn session_path(&self) -> PathBuf {
        if self.session.trim().is_empty() {
            PathBuf::from(self.root.trim()).join("session.json")
        } else {
            PathBuf::from(self.session.trim())
        }
    }

    /// The plan inputs, or a message naming the one thing that is missing. Nothing is defaulted
    /// here that would let an export run against the wrong account or write somewhere unintended.
    pub fn options(&self) -> Result<Options, String> {
        if self.account == 0 {
            return Err("还没有填写账号 ID".to_string());
        }
        if self.root.trim().is_empty() {
            return Err("还没有选择导出目录".to_string());
        }
        let session = self.session_path();
        if !session.is_file() {
            return Err(
                "还没有会话文件：点“自动获取会话”从正在运行的 EVPlayer2 里读一份".to_string(),
            );
        }
        let extension = self.extension.to_ascii_lowercase();
        if !matches!(extension.as_str(), "mp4" | "mkv") {
            return Err(format!("输出格式只能是 mp4 或 mkv，现在是 {extension}"));
        }
        // Absolute, always. A run's working directory is the lesson's `--work`, so a relative
        // output path would be resolved *there* — the file would land somewhere nobody looks and
        // the queue's own "already exported" check would never see it.
        let root = absolute(&self.root)?;
        Ok(Options {
            session,
            account: self.account,
            root,
            jobs: self.jobs.clamp(1, 32),
            extension,
            ffmpeg: self.ffmpeg.clone(),
            ffprobe: self.ffprobe.clone(),
            force: self.force,
            stub_tail_ms: 0,
        })
    }
}

/// Turn a user-typed directory into an absolute one, creating it so a typo in a parent directory
/// is caught before a batch rather than during it.
///
/// Deliberately not `canonicalize`: on Windows that returns a `\\?\` verbatim path, which is
/// correct for the filesystem but ugly in every log line, argv preview and error message the user
/// reads.
fn absolute(directory: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(directory.trim());
    if path.as_os_str().is_empty() {
        return Err("还没有选择导出目录".to_string());
    }
    let path = if path.is_absolute() {
        path
    } else {
        std::env::current_dir()
            .map_err(|error| format!("无法确定当前目录：{error}"))?
            .join(path)
    };
    std::fs::create_dir_all(&path)
        .map_err(|error| format!("无法创建导出目录 {}：{error}", path.display()))?;
    Ok(path)
}

pub fn config_path() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    base.join("evmedia-gui").join("config.json")
}

/// A missing or unreadable config is not an error — it just means "no preferences yet".
///
/// Everything that arrives from the frontend goes through `with_defaults`, so the absolute export
/// root and the resolved session path are the same no matter which command asked.
pub fn load() -> GuiConfig {
    let stored: Option<GuiConfig> = std::fs::read(config_path())
        .ok()
        .and_then(|bytes| serde_json::from_slice(crate::strip_bom(&bytes)).ok());
    stored.unwrap_or_default().with_defaults()
}

pub fn save(config: &GuiConfig) -> Result<(), String> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let bytes = serde_json::to_vec_pretty(config).map_err(|error| error.to_string())?;
    std::fs::write(&path, bytes).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ready() -> GuiConfig {
        let path = std::env::temp_dir().join("evmedia-config-test-session.json");
        std::fs::write(&path, "{}").unwrap();
        GuiConfig {
            session: path.display().to_string(),
            account: 119354,
            root: std::env::temp_dir().display().to_string(),
            ..GuiConfig::default().with_defaults()
        }
    }

    #[test]
    fn defaults_fill_in_only_what_is_missing() {
        let config = GuiConfig { workers: 9, extension: "mkv".into(), ..Default::default() }
            .with_defaults();
        assert_eq!(config.workers, 9);
        assert_eq!(config.extension, "mkv");
        assert_eq!(config.jobs, DEFAULT_JOBS);
        assert_eq!(config.ffmpeg, "ffmpeg");
    }

    #[test]
    fn a_fresh_install_exports_one_video_at_a_time() {
        assert_eq!(GuiConfig::default().with_defaults().workers, 1);
    }

    #[test]
    fn each_missing_input_is_named_before_anything_starts() {
        // No session file at all: the account is checked first, so give it one.
        let no_session = GuiConfig {
            account: 7,
            root: std::env::temp_dir().display().to_string(),
            ..GuiConfig::default().with_defaults()
        };
        let error: String = no_session.options().unwrap_err();
        assert!(error.contains("自动获取会话"), "unexpected message: {error}");

        // A session path that does not exist.
        let missing = GuiConfig {
            session: "no/such/session.json".into(),
            ..no_session.clone()
        };
        let error: String = missing.options().unwrap_err();
        assert!(error.contains("自动获取会话"), "unexpected message: {error}");

        // A session but no account.
        let ready = ready();
        let no_account = GuiConfig { account: 0, ..ready.clone() };
        let error: String = no_account.options().unwrap_err();
        assert!(error.contains("账号"), "unexpected message: {error}");

        // Account and session but nowhere to write.
        let no_root = GuiConfig { root: String::new(), ..ready };
        let error: String = no_root.options().unwrap_err();
        assert!(error.contains("导出目录"), "unexpected message: {error}");
    }

    /// A file that exists but is not a usable session is the CLI's problem to report; the window
    /// only promises the file is there.
    #[test]
    fn a_ready_config_produces_options() {
        let options = ready().options().unwrap();
        assert_eq!(options.account, 119354);
        assert_eq!(options.extension, "mp4");
        assert!(!options.force);
    }

    /// The run's child is started with the lesson's work directory as its working directory, so a
    /// relative root would put every output file inside that work directory instead of `out/`.
    #[test]
    fn the_export_root_is_always_absolute() {
        let relative = GuiConfig {
            root: "evmedia-relative-root-check".to_string(),
            ..ready()
        };
        let options = relative.options().unwrap();
        assert!(options.root.is_absolute(), "{} is not absolute", options.root.display());
        assert!(options.root.is_dir());
        let item = crate::plan::item_of(
            &options,
            &crate::catalog::Found {
                course: 1,
                file: 2,
                title: "t".into(),
                duration_seconds: None,
                path: Vec::new(),
            },
        );
        assert!(item.output.is_absolute() && item.work.is_absolute());
        assert!(item.output.starts_with(&options.root));
        let _ = std::fs::remove_dir_all(&options.root);
    }

    /// The window sends whatever is in its text boxes, so a relative root has to be resolved by the
    /// command that receives it — otherwise "where the session is written" and "where it is looked
    /// for" are two different directories.
    #[test]
    fn normalising_resolves_a_relative_root_once() {
        let relative = GuiConfig {
            root: "evmedia-normalise-check".to_string(),
            ..ready()
        };
        let normalised = relative.normalized();
        assert!(normalised.root.starts_with(std::env::current_dir().unwrap().to_string_lossy().as_ref()));
        assert_eq!(normalised.session_path(), normalised.normalized().session_path());
        // An empty root stays empty: it means "the user has not chosen one yet", not "here".
        let empty = GuiConfig { root: String::new(), ..relative };
        assert!(empty.normalized().root.is_empty());
        let _ = std::fs::remove_dir_all(normalised.root);
    }

    #[test]
    fn a_bad_container_is_refused_here_rather_than_by_ffmpeg() {
        let config = GuiConfig { extension: "avi".into(), ..ready() };
        let error: String = config.options().unwrap_err();
        assert!(error.contains("mp4"));
    }
}
