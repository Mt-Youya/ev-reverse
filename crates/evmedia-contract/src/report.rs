//! The two output channels, kept strictly apart.
//!
//! `info` carries everything a human would read; `event` carries the machine channel. With
//! `--json-events` off, `info` prints to stdout exactly as the pre-workspace CLI did and
//! `event` does nothing — so `evmedia grab … | tee log` is byte-for-byte unchanged. With the
//! flag on, stdout becomes a pure JSON-lines channel and `info` moves to stderr, which is what
//! keeps the GUI's log pane a faithful transcript of a terminal run.

use crate::event::Event;
use std::{
    io::Write,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReporterMode {
    /// Human text on stdout; events discarded.
    Human,
    /// Events on stdout, human text on stderr.
    Json,
    /// Everything discarded. For tests.
    Null,
}

pub struct Reporter {
    mode: ReporterMode,
    stop: Option<PathBuf>,
}

impl Reporter {
    pub fn new(mode: ReporterMode, stop: Option<PathBuf>) -> Self {
        Self { mode, stop }
    }

    /// The default: exactly the pre-workspace behaviour.
    pub fn human() -> Self {
        Self::new(ReporterMode::Human, None)
    }

    /// Machine channel on, human channel redirected to stderr.
    pub fn json(stop: Option<PathBuf>) -> Self {
        Self::new(ReporterMode::Json, stop)
    }

    pub fn silent() -> Self {
        Self::new(ReporterMode::Null, None)
    }

    /// Pick the mode from the parsed global flags.
    pub fn from_cli(json_events: bool, stop_file: Option<PathBuf>) -> Self {
        if json_events {
            Self::json(stop_file)
        } else {
            Self::new(ReporterMode::Human, stop_file)
        }
    }

    pub fn mode(&self) -> ReporterMode {
        self.mode
    }

    /// A line of human-readable output.
    pub fn info(&self, message: impl AsRef<str>) {
        match self.mode {
            ReporterMode::Human => println!("{}", message.as_ref()),
            ReporterMode::Json => eprintln!("{}", message.as_ref()),
            ReporterMode::Null => {}
        }
    }

    /// A structured event. No-op unless the machine channel is on.
    pub fn event(&self, event: &Event) {
        if self.mode != ReporterMode::Json {
            return;
        }
        if let Ok(line) = serde_json::to_string(event) {
            let stdout = std::io::stdout();
            let mut lock = stdout.lock();
            // A lost event is worse than a slow one: the GUI would sit on a stale progress bar.
            let _ = writeln!(lock, "{line}");
            let _ = lock.flush();
        }
    }

    /// True once the cancellation file exists. Polled at loop boundaries.
    pub fn stopped(&self) -> bool {
        self.stop.as_ref().is_some_and(|path| path.exists())
    }

    pub fn stop_path(&self) -> Option<&Path> {
        self.stop.as_deref()
    }

    /// Sleep that gives up early when cancellation is requested, so the GUI's stop button is
    /// noticed within a fraction of a second instead of a whole poll interval.
    pub fn sleep(&self, total: std::time::Duration) {
        const SLICE: std::time::Duration = std::time::Duration::from_millis(250);
        let start = std::time::Instant::now();
        while start.elapsed() < total {
            if self.stopped() {
                return;
            }
            let left = total.saturating_sub(start.elapsed());
            std::thread::sleep(left.min(SLICE));
        }
    }
}
