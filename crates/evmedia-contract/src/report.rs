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
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
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

#[derive(Clone)]
pub struct Reporter {
    mode: ReporterMode,
    stop: Option<PathBuf>,
    /// Every event this reporter emitted, when a caller asked to see them.
    ///
    /// The machine channel is otherwise write-only stdout, which leaves behaviour whose only
    /// effect is an event — the attempt cap on a segment that cannot be decrypted, for one —
    /// observable to nobody, and therefore pinnable by no test.
    sink: Option<Arc<Mutex<Vec<Event>>>>,
    /// Whether a terminal `Finished` has gone out.
    ///
    /// The contract says `finished` is the last line of *every* run. Only `grab` used to emit one,
    /// so a successful `export-evs` ended with a `stage` line and no verdict at all — and a reader
    /// that refuses to call a run successful without a reported status read every finished export
    /// as a failure. This flag is what lets `main` guarantee the line without emitting it twice for
    /// the commands that do report their own.
    finished: Arc<AtomicBool>,
}

impl Reporter {
    pub fn new(mode: ReporterMode, stop: Option<PathBuf>) -> Self {
        Self { mode, stop, sink: None, finished: Arc::new(AtomicBool::new(false)) }
    }

    /// A reporter that is silent on both channels and also records every event it was given.
    ///
    /// The returned handle sees events as they are emitted, so a test can assert on the machine
    /// channel itself rather than on its side effects.
    pub fn capturing() -> (Self, Arc<Mutex<Vec<Event>>>) {
        let sink = Arc::new(Mutex::new(Vec::new()));
        let reporter = Self {
            mode: ReporterMode::Null,
            stop: None,
            sink: Some(Arc::clone(&sink)),
            finished: Arc::new(AtomicBool::new(false)),
        };
        (reporter, sink)
    }

    /// Whether a terminal event has already been reported, so a caller can supply the one the
    /// contract requires without duplicating it.
    pub fn finished(&self) -> bool {
        self.finished.load(Ordering::SeqCst)
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

    /// A structured event. No-op unless the machine channel is on, or a sink was asked for.
    pub fn event(&self, event: &Event) {
        if matches!(event, Event::Finished { .. }) {
            self.finished.store(true, Ordering::SeqCst);
        }
        if let Some(sink) = &self.sink {
            if let Ok(mut events) = sink.lock() {
                events.push(event.clone());
            }
        }
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
