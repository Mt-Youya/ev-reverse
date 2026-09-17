use crate::ToArgv;
use std::path::PathBuf;

#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub struct ExportArgs {
    /// Original complete VOD M3U8 (must include ENDLIST).
    #[arg(long)]
    pub playlist: PathBuf,
    /// Local token/playkey JSON or a captured events.jsonl with a matching video session.
    #[arg(long)]
    pub session: PathBuf,
    #[arg(long)]
    pub output: PathBuf,
    #[arg(long, default_value = "tools/parser-tools/captured/rust-export")]
    pub work: PathBuf,
    #[arg(long)]
    pub cache: Option<PathBuf>,
    #[arg(long, default_value_t = 8)]
    pub jobs: usize,
    #[arg(long, default_value = "ffmpeg")]
    pub ffmpeg: String,
    #[arg(long, default_value = "ffprobe")]
    pub ffprobe: String,
}

impl ToArgv for ExportArgs {
    fn to_argv(&self) -> Vec<String> {
        let mut argv = vec!["export-video".into()];
        for (flag, path) in [("--playlist", &self.playlist), ("--session", &self.session),
                             ("--output", &self.output), ("--work", &self.work)] {
            argv.extend([flag.into(), path.to_string_lossy().into_owned()]);
        }
        if let Some(path) = &self.cache {
            argv.extend(["--cache".into(), path.to_string_lossy().into_owned()]);
        }
        argv.extend(["--jobs".into(), self.jobs.to_string(), "--ffmpeg".into(), self.ffmpeg.clone(),
                     "--ffprobe".into(), self.ffprobe.clone()]);
        argv
    }
}
