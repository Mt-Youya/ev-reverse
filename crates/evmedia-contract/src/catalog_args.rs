use crate::ToArgv;
use std::path::PathBuf;

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
        argv
    }
}
