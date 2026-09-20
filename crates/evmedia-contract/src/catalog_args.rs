use crate::ToArgv;
use clap::ValueEnum;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Which part of an EVS export to run. The GUI downloads a whole batch first, then converts it.
#[derive(clap::ValueEnum, Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExportPhase {
    All,
    Download,
    Convert,
}

/// A persisted batch request.  The desktop app writes it once and the CLI owns the complete
/// cross-video segment scheduler, so individual GUI child processes never create isolated queues.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExportBatchPlan {
    pub videos: Vec<ExportBatchVideo>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExportBatchVideo {
    pub id: String,
    pub course: i64,
    pub file: i64,
    pub output: PathBuf,
    pub work: PathBuf,
}

#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct ExportBatchArgs {
    #[arg(long)]
    pub session: PathBuf,
    #[arg(long)]
    pub account: i64,
    /// JSON file containing every selected lesson's identifiers and output paths.
    #[arg(long)]
    pub plan: PathBuf,
    #[arg(long, default_value_t = 8)]
    pub download_jobs: usize,
    #[arg(long, default_value_t = 2)]
    pub decrypt_jobs: usize,
    #[arg(long, default_value = "ffmpeg")]
    pub ffmpeg: String,
    #[arg(long, default_value = "ffprobe")]
    pub ffprobe: String,
    #[arg(long)]
    pub force: bool,
}

/// Credentials captured from the running EVPlayer2 session, plus a destination for the tree.
#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct CatalogArgs {
    #[arg(long)]
    pub session: PathBuf,
    #[arg(long)]
    pub account: i64,
    #[arg(long)]
    pub output: PathBuf,
}

/// Download one authorized EVS file and decrypt its embedded complete M3U8.
#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct DownloadEvsArgs {
    #[arg(long)]
    pub session: PathBuf,
    #[arg(long)]
    pub account: i64,
    #[arg(long)]
    pub course: i64,
    #[arg(long)]
    pub file: i64,
    #[arg(long)]
    pub output: PathBuf,
}

/// Download, authorize, decrypt and remux one EVS video to a normal container.
#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct ExportEvsArgs {
    #[arg(long)]
    pub session: PathBuf,
    #[arg(long)]
    pub account: i64,
    #[arg(long)]
    pub course: i64,
    #[arg(long)]
    pub file: i64,
    #[arg(long)]
    pub output: PathBuf,
    #[arg(long, default_value = "tools/parser-tools/captured/rust-evs-export")]
    pub work: PathBuf,
    #[arg(long, default_value_t = 8)]
    pub jobs: usize,
    #[arg(long, default_value = "ffmpeg")]
    pub ffmpeg: String,
    #[arg(long, default_value = "ffprobe")]
    pub ffprobe: String,
    /// Overwrite an existing output file instead of refusing to run.
    #[arg(long)]
    pub force: bool,
    /// Run the complete export, only fetch encrypted segments, or only convert cached segments.
    #[arg(long, value_enum, default_value_t = ExportPhase::All)]
    pub phase: ExportPhase,
}

impl ToArgv for CatalogArgs {
    fn to_argv(&self) -> Vec<String> {
        vec!["catalog".into(), "--session".into(), self.session.to_string_lossy().into_owned(),
             "--account".into(), self.account.to_string(), "--output".into(),
             self.output.to_string_lossy().into_owned()]
    }
}

impl ToArgv for DownloadEvsArgs {
    fn to_argv(&self) -> Vec<String> {
        vec!["download-evs".into(), "--session".into(), self.session.to_string_lossy().into_owned(),
             "--account".into(), self.account.to_string(), "--course".into(), self.course.to_string(),
             "--file".into(), self.file.to_string(), "--output".into(),
             self.output.to_string_lossy().into_owned()]
    }
}

impl ToArgv for ExportEvsArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["export-evs".into(), "--session".into(), self.session.to_string_lossy().into_owned(),
             "--account".into(), self.account.to_string(), "--course".into(), self.course.to_string(),
             "--file".into(), self.file.to_string(), "--output".into(), self.output.to_string_lossy().into_owned(),
             "--work".into(), self.work.to_string_lossy().into_owned(), "--jobs".into(), self.jobs.to_string(),
             "--ffmpeg".into(), self.ffmpeg.clone(), "--ffprobe".into(), self.ffprobe.clone()];
        if self.force {
            argv.push("--force".into());
        }
        argv.extend(["--phase".into(), self.phase.to_possible_value().unwrap().get_name().into()]);
        argv
    }
}

impl ToArgv for ExportBatchArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["export-batch".into(), "--session".into(), self.session.to_string_lossy().into_owned(),
            "--account".into(), self.account.to_string(), "--plan".into(), self.plan.to_string_lossy().into_owned(),
            "--download-jobs".into(), self.download_jobs.to_string(), "--decrypt-jobs".into(), self.decrypt_jobs.to_string(),
            "--ffmpeg".into(), self.ffmpeg.clone(), "--ffprobe".into(), self.ffprobe.clone()];
        if self.force { argv.push("--force".into()); }
        argv
    }
}
