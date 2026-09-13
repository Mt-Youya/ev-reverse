//! The argv shape of `evmedia`, defined exactly once.
//!
//! The CLI parses these with `Cli::parse()`; the GUI constructs the same structs and calls
//! `to_argv()`. Because there is only one definition, the two cannot disagree about what a
//! legal invocation is. `ToArgv` is the inverse of clap's parsing, and the round-trip test in
//! `tests/argv_roundtrip.rs` holds the two ends together.

use clap::{Parser, Subcommand};
use std::path::PathBuf;

/// Defaults live here and nowhere else: the GUI reads them back out of the clap definition
/// rather than repeating them, so a change here reaches every surface at once.
pub const DEFAULT_PARALLEL: usize = 8;
pub const DEFAULT_OUTPUT: &str = "ev2_out";
pub const DEFAULT_JOBS: usize = 8;
pub const DEFAULT_POLL_SECS: u64 = 6;
pub const DEFAULT_IDLE_LIMIT: usize = 60;
pub const DEFAULT_ATTEMPTS: usize = 3;
pub const DEFAULT_SWEEP_GAP_MS: u64 = 30;
/// Sweep defaults for `recover`, measured against a live player rather than guessed: a 100 ms gap
/// held up at ~12 segments/s, about nine times real-time playback.
pub const DEFAULT_RECOVER_BATCH: usize = 60;
pub const DEFAULT_RECOVER_GAP_MS: u64 = 100;
pub const DEFAULT_RECOVER_IDLE: usize = 3;

#[derive(Parser, Debug, Clone, PartialEq, Eq)]
#[command(
    name = "evmedia",
    version,
    about = "EVPlayer2 catalogue, download and live-manifest tool"
)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,

    /// Write newline-delimited JSON events to stdout and move human-readable output to stderr.
    #[arg(long, global = true)]
    pub json_events: bool,

    /// Stop cleanly as soon as this file exists. Used by the GUI to cancel a running job.
    #[arg(long, global = true)]
    pub stop_file: Option<PathBuf>,
}

#[derive(Subcommand, Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// Print a course/video directory tree from a JSON catalog.
    Tree(TreeArgs),
    /// Download all listed segments concurrently with resume-safe atomic files.
    Download(DownloadArgs),
    /// Decode an EVPlayer2 5.0.5 segment ZIP/directory using a live-captured manifest.
    DecodeEv(DecodeEvArgs),
    /// Create a video-specific EVPlayer2 5.0.5 manifest from an active Windows process.
    CaptureEv(CaptureEvArgs),
    /// Harvest segment keys and signed URLs from a live player, then download, decrypt and merge.
    Grab(GrabArgs),
    /// Recover segment keys from a live player's memory and decrypt the segments it already
    /// downloaded, without fetching anything again.
    Recover(RecoverArgs),
    /// Report adapters compiled into this portable core.
    Adapters(AdaptersArgs),
}

#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct TreeArgs {
    pub catalog: PathBuf,
}

#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct DownloadArgs {
    pub manifest: PathBuf,
    pub output: PathBuf,
    #[arg(long, default_value_t = DEFAULT_PARALLEL)]
    pub parallel: usize,
}

#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct DecodeEvArgs {
    pub input: PathBuf,
    pub manifest: PathBuf,
    pub output: PathBuf,
}

#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct CaptureEvArgs {
    /// PID of the running EVPlayer2 process.
    #[arg(long)]
    pub pid: u32,
    /// Directory or ZIP holding the encrypted segments for this lesson.
    #[arg(long)]
    pub input: PathBuf,
    #[arg(long)]
    pub output: PathBuf,
}

#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct GrabArgs {
    /// PID of the running EVPlayer2 process.
    #[arg(long)]
    pub pid: u32,
    #[arg(long, default_value = DEFAULT_OUTPUT)]
    pub output: PathBuf,
    #[arg(long, default_value_t = DEFAULT_JOBS)]
    pub jobs: usize,
    /// Seconds between memory polls.
    #[arg(long, default_value_t = DEFAULT_POLL_SECS)]
    pub poll: u64,
    /// Give up after this many polls with no new segment decrypted.
    #[arg(long, default_value_t = DEFAULT_IDLE_LIMIT)]
    pub idle_limit: usize,
    #[arg(long, default_value_t = DEFAULT_ATTEMPTS)]
    pub attempts: usize,
    /// Do not steer the playhead back over gaps the player skipped.
    #[arg(long)]
    pub no_sweep: bool,
    /// Delay between posted seek keystrokes, in milliseconds.
    #[arg(long, default_value_t = DEFAULT_SWEEP_GAP_MS)]
    pub sweep_gap_ms: u64,
    /// Remux the finished lesson to MP4 with ffmpeg.
    #[arg(long)]
    pub mp4: bool,
}

/// Decrypt segments the player already downloaded, using keys read out of its live memory.
///
/// There is nothing to download here: the player's own cache *is* the encrypted data, and the
/// keys for the segments it has decrypted for playback are still sitting in its heap.
#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct RecoverArgs {
    /// PID of the running EVPlayer2 process. Defaults to the only EVPlayer2 that is running.
    #[arg(long)]
    pub pid: Option<u32>,
    /// The directory the player downloaded its segments into.
    #[arg(long)]
    pub cache: PathBuf,
    #[arg(long)]
    pub output: PathBuf,
    /// Remux the finished lesson to MP4 with ffmpeg.
    #[arg(long)]
    pub mp4: bool,
    /// Walk the playhead across the lesson with the step-forward key, collecting keys as it goes.
    ///
    /// Without this, only the segments the player has already decrypted are recoverable.
    #[arg(long)]
    pub sweep: bool,
    /// Step-forward presses posted per sweep round.
    #[arg(long, default_value_t = DEFAULT_RECOVER_BATCH)]
    pub sweep_batch: usize,
    /// Delay between posted presses, in milliseconds.
    #[arg(long, default_value_t = DEFAULT_RECOVER_GAP_MS)]
    pub sweep_gap_ms: u64,
    /// Stop after this many consecutive rounds that produce no new key.
    #[arg(long, default_value_t = DEFAULT_RECOVER_IDLE)]
    pub sweep_idle: usize,
}

#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct AdaptersArgs {}

/// Render a value back into the argv tokens clap would have parsed it from.
pub trait ToArgv {
    fn to_argv(&self) -> Vec<String>;
}

/// The clap definition, for surfaces that need to introspect it: the GUI reads defaults and
/// option names from here, and so does the drift test.
pub fn command() -> clap::Command {
    use clap::CommandFactory;
    Cli::command()
}

/// Parse an argv exactly as the CLI will, prefixed the way a real invocation is.
///
/// A caller that must not spawn something illegal can ask the CLI's own parser instead of
/// duplicating the rules — and does not have to depend on clap to do it.
pub fn try_parse(argv: &[String]) -> Result<Cli, String> {
    use clap::Parser;
    let mut full = Vec::with_capacity(argv.len() + 1);
    full.push("evmedia".to_string());
    full.extend_from_slice(argv);
    Cli::try_parse_from(&full).map_err(|error| error.to_string())
}

/// Every option is written out even when it holds its default, so the argv shown in the GUI is
/// the complete, literal description of what will run. Boolean flags are the exception: only
/// their presence carries meaning, so a `false` flag emits nothing.
fn push_path(argv: &mut Vec<String>, flag: &str, value: &std::path::Path) {
    argv.push(flag.to_string());
    argv.push(value.to_string_lossy().into_owned());
}

fn push_pos(argv: &mut Vec<String>, value: &std::path::Path) {
    argv.push(value.to_string_lossy().into_owned());
}

fn push_num(argv: &mut Vec<String>, flag: &str, value: impl std::fmt::Display) {
    argv.push(flag.to_string());
    argv.push(value.to_string());
}

fn push_flag(argv: &mut Vec<String>, flag: &str, on: bool) {
    if on {
        argv.push(flag.to_string());
    }
}

impl ToArgv for Command {
    fn to_argv(&self) -> Vec<String> {
        match self {
            Command::Tree(args) => args.to_argv(),
            Command::Download(args) => args.to_argv(),
            Command::DecodeEv(args) => args.to_argv(),
            Command::CaptureEv(args) => args.to_argv(),
            Command::Grab(args) => args.to_argv(),
            Command::Recover(args) => args.to_argv(),
            Command::Adapters(args) => args.to_argv(),
        }
    }
}

impl ToArgv for TreeArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["tree".to_string()];
        push_pos(&mut argv, &self.catalog);
        argv
    }
}

impl ToArgv for DownloadArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["download".to_string()];
        push_pos(&mut argv, &self.manifest);
        push_pos(&mut argv, &self.output);
        push_num(&mut argv, "--parallel", self.parallel);
        argv
    }
}

impl ToArgv for DecodeEvArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["decode-ev".to_string()];
        push_pos(&mut argv, &self.input);
        push_pos(&mut argv, &self.manifest);
        push_pos(&mut argv, &self.output);
        argv
    }
}

impl ToArgv for CaptureEvArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["capture-ev".to_string()];
        push_num(&mut argv, "--pid", self.pid);
        push_path(&mut argv, "--input", &self.input);
        push_path(&mut argv, "--output", &self.output);
        argv
    }
}

impl ToArgv for GrabArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["grab".to_string()];
        push_num(&mut argv, "--pid", self.pid);
        push_path(&mut argv, "--output", &self.output);
        push_num(&mut argv, "--jobs", self.jobs);
        push_num(&mut argv, "--poll", self.poll);
        push_num(&mut argv, "--idle-limit", self.idle_limit);
        push_num(&mut argv, "--attempts", self.attempts);
        push_flag(&mut argv, "--no-sweep", self.no_sweep);
        push_num(&mut argv, "--sweep-gap-ms", self.sweep_gap_ms);
        push_flag(&mut argv, "--mp4", self.mp4);
        argv
    }
}

impl ToArgv for RecoverArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["recover".to_string()];
        if let Some(pid) = self.pid {
            push_num(&mut argv, "--pid", pid);
        }
        push_path(&mut argv, "--cache", &self.cache);
        push_path(&mut argv, "--output", &self.output);
        push_flag(&mut argv, "--mp4", self.mp4);
        push_flag(&mut argv, "--sweep", self.sweep);
        push_num(&mut argv, "--sweep-batch", self.sweep_batch);
        push_num(&mut argv, "--sweep-gap-ms", self.sweep_gap_ms);
        push_num(&mut argv, "--sweep-idle", self.sweep_idle);
        argv
    }
}

impl ToArgv for AdaptersArgs {
    fn to_argv(&self) -> Vec<String> {
        vec!["adapters".to_string()]
    }
}