//! The machine channel between the CLI and the GUI.
//!
//! With `--json-events` the CLI writes one of these per line to stdout. The GUI parses them to
//! drive its progress bar and status. Human-readable text moves to stderr in that mode, so the
//! two channels never interleave and the terminal experience is unchanged when the flag is off.
//!
//! Forward compatibility rests on one rule the GUI must honour: **a line that fails to parse is
//! treated as plain log text, not as an error.** That is what lets a newer CLI add event
//! variants without breaking an older GUI.

use serde::{Deserialize, Serialize};

/// Bumped when the shape of [`Event`] changes incompatibly. The GUI refuses to drive a CLI
/// whose protocol it does not recognise rather than guessing.
pub const PROTOCOL_V1: u32 = 1;

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum Event {
    /// First line of every run. `command` is the argv the CLI actually received.
    Started {
        protocol: u32,
        command: Vec<String>,
        app_version: String,
    },
    /// A phase boundary, so the GUI can label what the progress bar is measuring.
    Stage {
        name: Stage,
        state: StageState,
        #[serde(default)]
        detail: String,
    },
    /// One sample of the harvest counters, emitted once per poll.
    Progress {
        stage: Stage,
        keys: usize,
        urls: usize,
        segments: usize,
        done: usize,
        failed: usize,
        elapsed_secs: u64,
    },
    /// A single segment changed state. Emitted immediately, to keep the UI responsive.
    Segment {
        index: u32,
        file: String,
        state: SegmentState,
        attempt: usize,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    /// A human-readable line the CLI would otherwise have printed. Carried separately so the
    /// GUI can show a faithful transcript of what a terminal user would have seen.
    Log { level: Level, message: String },
    /// A file was produced.
    Artifact {
        kind: ArtifactKind,
        path: String,
        bytes: u64,
    },
    /// Last line of every run. `exit_code` mirrors the process exit status.
    Finished {
        status: Status,
        exit_code: i32,
        #[serde(default)]
        message: String,
    },
}

#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Scan,
    Download,
    Merge,
    Remux,
}

#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StageState {
    Begin,
    End,
}

#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SegmentState {
    Done,
    Failed,
}

#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactKind {
    Ts,
    Mp4,
    Manifest,
}

#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Level {
    Info,
    Warn,
}

/// The outcome of a run, in more detail than an exit code can carry.
#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    /// Every segment of the lesson was decrypted and merged.
    Complete,
    /// The run ended with holes in the lesson; the merge is a `.partial.ts`.
    Partial,
    /// Nothing was harvested at all.
    Nothing,
    /// Stopped via the stop file.
    Cancelled,
    /// A runtime error ended the run.
    Failed,
}
