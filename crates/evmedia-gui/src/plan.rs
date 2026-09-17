//! Turning a catalog selection into `export-evs` invocations.
//!
//! The window's whole model of an export is this file: which argv runs, where its output goes, where
//! its scratch space lives. Nothing here downloads, decrypts or decides anything the CLI already
//! decides — the argv is built from `evmedia-contract`'s own structs and is re-parsed through the
//! CLI's parser before anything is spawned.

use evmedia_contract::{Command, ExportEvsArgs, ToArgv};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// A remembered export request. Persisted as part of the queue so a batch survives a restart of the
/// window: the downloaded segments under `--work` are resumed, not re-fetched.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct JobItem {
    pub id: String,
    pub course: i64,
    pub file: i64,
    pub title: String,
    #[serde(default)]
    pub duration_seconds: Option<f64>,
    #[serde(default)]
    pub path: Vec<String>,
    pub output: PathBuf,
    pub work: PathBuf,
}

#[derive(Clone, Debug)]
pub struct Options {
    pub session: PathBuf,
    pub account: i64,
    pub root: PathBuf,
    pub jobs: usize,
    pub extension: String,
    pub ffmpeg: String,
    pub ffprobe: String,
    pub force: bool,
    /// How long the stand-in CLI should dawdle after its last segment, in milliseconds.
    ///
    /// It exists so a test can stop a batch while it is genuinely running, without setting a
    /// process-wide environment variable that every other test's child would also inherit. The real
    /// CLI ignores it.
    pub stub_tail_ms: u64,
}

/// One catalog entry, as a queued job. `--work` gets a directory per video: the CLI would accept
/// one shared directory, but per-video directories make a single lesson's scratch space easy to
/// inspect, and one video's leftovers can never be mistaken for another's.
pub fn item_of(options: &Options, video: &crate::catalog::Found) -> JobItem {
    JobItem {
        id: video.id(),
        course: video.course,
        file: video.file,
        title: video.title.clone(),
        duration_seconds: video.duration_seconds,
        path: video.path.clone(),
        output: output_of(&options.root, &video.path, &video.title, &options.extension),
        work: work_of(&options.root, video.course, video.file),
    }
}

/// The argv for one requested export. `session`, `account`, `course` and `file` come straight from
/// the catalog entry, so an export can never address a video the directory did not name.
pub fn argv(item: &JobItem, options: &Options) -> Vec<String> {
    let command = Command::ExportEvs(ExportEvsArgs {
        session: options.session.clone(),
        account: options.account,
        course: item.course,
        file: item.file,
        output: item.output.clone(),
        work: item.work.clone(),
        jobs: options.jobs,
        ffmpeg: options.ffmpeg.clone(),
        ffprobe: options.ffprobe.clone(),
        force: options.force,
    });
    command.to_argv()
}

/// `out/<文件夹...>/<标题>.<ext>` — the catalog's own structure, so a lesson keeps the place it has
/// in the course.
///
/// Two things about that structure are load-bearing:
///
/// - **The folder titles are the path.** Every course restarts its numbering at `01.`, so a flat
///   `out/<course>/` directory collects a dozen different `01. xxx.mp4` files from a dozen chapters.
///   The chapter is what makes the name unique, and it is already in the catalog.
/// - **The leaf title is the filename.** Lesson titles usually carry a container extension (that is
///   the filename the API returns), so it is dropped before the real one is appended — appending
///   blindly produced `01. 课程导言.mp4.mp4`.
pub fn output_of(root: &Path, folders: &[String], title: &str, extension: &str) -> PathBuf {
    let mut path = root.join("out");
    for folder in collapse(folders) {
        path.push(sanitize(&folder));
    }
    path.join(leaf_name(title, extension))
}

/// The folder chain with consecutive repeats folded away.
///
/// The catalog repeats a level whenever a course is nested under a folder of its own name
/// (`前端课程/前端课程/求职之道极速版/求职之道极速版/01.必看导言`), which is correct as a tree and
/// absurd as a path. Only *consecutive* repeats are collapsed, because those are the ones the tree
/// duplicates; a name that legitimately reappears deeper is kept.
fn collapse(folders: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::with_capacity(folders.len());
    for folder in folders {
        let name = folder.trim();
        if name.is_empty() {
            continue;
        }
        if out.last().map(|last| last == name).unwrap_or(false) {
            continue;
        }
        out.push(name.to_string());
    }
    out
}

/// The filename for one lesson: its own title, with any container extension it already carries
/// replaced by the one the user asked for.
pub fn leaf_name(title: &str, extension: &str) -> String {
    format!("{}.{extension}", sanitize(strip_container(title)))
}

/// A title without a container extension, `.mp4`/`.mkv`/`.flv`/… — anything that looks like one.
fn strip_container(title: &str) -> &str {
    let trimmed = title.trim_end();
    match trimmed.rsplit_once('.') {
        Some((stem, suffix))
            if (2..=4).contains(&suffix.len())
                && suffix.chars().all(|c| c.is_ascii_alphanumeric())
                && !stem.is_empty() =>
        {
            stem
        }
        _ => trimmed,
    }
}

/// Scratch space per video. Segment files, the EVS descriptor and the merged `.ts` all land here,
/// and this is what makes a rerun cheap: `download` reuses any segment already on disk.
pub fn work_of(root: &Path, course: i64, file: i64) -> PathBuf {
    root.join("work").join(format!("{course}-{file}"))
}

/// A Windows filename cannot contain `\/:*?"<>|` or end in a dot or space, and the whole path wants
/// to stay under the classic 260-character limit. Anything illegal becomes `_`; the result is capped
/// so a long lesson title cannot push the file past MAX_PATH.
pub fn sanitize(title: &str) -> String {
    const MAX: usize = 120;
    let mut out = String::with_capacity(title.len());
    for character in title.chars() {
        match character {
            '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => out.push('_'),
            // Control characters are legal in the bytes but not in a path.
            character if (character as u32) < 0x20 => out.push('_'),
            character => out.push(character),
        }
    }
    let trimmed = out.trim().trim_end_matches('.').trim();
    let mut capped: String = trimmed.chars().take(MAX).collect();
    if capped.is_empty() {
        // Nothing but dots and spaces is a path element, not a name. `..` as a folder would walk out
        // of the export root every other name stays inside, so it is replaced before the default
        // name is considered.
        capped = if out.trim().chars().all(|character| character == '.') && !out.trim().is_empty() {
            "_".repeat(out.trim().chars().count())
        } else {
            "video".to_string()
        };
    }
    // Reserved device names are still reserved with an extension.
    const RESERVED: &[&str] = &[
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
        "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ];
    if RESERVED.iter().any(|name| name.eq_ignore_ascii_case(&capped)) {
        capped.push('_');
    }
    capped
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;
    use evmedia_contract::Cli;

    fn item() -> JobItem {
        JobItem {
            id: "315187:903780".into(),
            course: 315187,
            file: 903780,
            title: "2-2. React和Vue描述页面的区别".into(),
            duration_seconds: Some(1908.9),
            path: vec!["第二章".into()],
            output: PathBuf::from("root/out/315187/x.mp4"),
            work: PathBuf::from("root/work/315187-903780"),
        }
    }

    fn options() -> Options {
        Options {
            session: PathBuf::from("session.json"),
            account: 119354,
            root: PathBuf::from("root"),
            jobs: 8,
            extension: "mp4".into(),
            ffmpeg: "ffmpeg".into(),
            ffprobe: "ffprobe".into(),
            force: false,
            stub_tail_ms: 0,
        }
    }

    /// The point of building the argv from the shared structs is that the CLI's own parser accepts
    /// it. If this fails, the window would spawn something the CLI rejects.
    #[test]
    fn the_argv_is_something_the_cli_parses() {
        let argv = argv(&item(), &options());
        let parsed = Cli::try_parse_from(std::iter::once("evmedia".to_string()).chain(argv.clone()))
            .unwrap_or_else(|error| panic!("{argv:?} did not parse: {error}"));
        match parsed.command {
            Command::ExportEvs(args) => {
                assert_eq!(args.course, 315187);
                assert_eq!(args.file, 903780);
                assert_eq!(args.account, 119354);
                assert_eq!(args.jobs, 8);
                assert!(!args.force);
            }
            other => panic!("expected export-evs, got {other:?}"),
        }
    }

    #[test]
    fn force_reaches_the_cli_only_when_asked() {
        let mut forced = options();
        forced.force = true;
        assert!(argv(&item(), &forced).contains(&"--force".to_string()));
        assert!(!argv(&item(), &options()).contains(&"--force".to_string()));
    }

    /// The output mirrors the catalog: every folder becomes a directory, the lesson's own title
    /// becomes the file. A flat directory per course collects a dozen different `01. xxx.mp4` from a
    /// dozen chapters, which is the numbering collision this shape exists to avoid.
    #[test]
    fn the_output_mirrors_the_catalog_structure() {
        let folders = vec!["前端课程".to_string(), "求职之道极速版".to_string(), "01.必看导言".to_string()];
        let path = output_of(Path::new("root"), &folders, "02. 表现力的训练.mp4", "mp4");
        assert_eq!(
            path,
            PathBuf::from("root/out/前端课程/求职之道极速版/01.必看导言/02. 表现力的训练.mp4")
        );
        // Two chapters that both number from `01.` produce the same filename in different
        // directories, which is the collision a flat per-course directory would create.
        let first = output_of(Path::new("root"), &folders, "01. 就业的核心问题.mp4", "mp4");
        let other = output_of(
            Path::new("root"),
            &["前端课程".to_string(), "求职之道极速版".to_string(), "02.简历".to_string()],
            "01. 就业的核心问题.mp4",
            "mp4",
        );
        assert_ne!(first, other);
        assert_eq!(first.file_name(), other.file_name());
        assert!(first.is_file() || !first.exists(), "the same name must not share a directory");
    }

    /// The catalog nests a course under a folder of its own name, so the raw chain repeats a level:
    /// `前端课程/前端课程/求职之道极速版/求职之道极速版/01.必看导言`. As a tree that is correct; as
    /// a path it is noise.
    #[test]
    fn consecutive_repeats_in_the_folder_chain_are_collapsed() {
        let folders = [
            "前端课程",
            "前端课程",
            "求职之道极速版",
            "求职之道极速版",
            "01.必看导言",
        ]
        .map(str::to_string);
        assert_eq!(
            collapse(&folders),
            vec!["前端课程", "求职之道极速版", "01.必看导言"]
        );
        // A name that legitimately reappears after something else is kept.
        let deeper = ["a", "b", "a"].map(str::to_string);
        assert_eq!(collapse(&deeper), vec!["a", "b", "a"]);
        // Empty levels are dropped rather than becoming a directory named "".
        let sparse = ["".to_string(), "x".to_string()];
        assert_eq!(collapse(&sparse), vec!["x"]);
    }

    #[test]
    fn work_is_still_keyed_by_the_lesson_ids() {
        assert_eq!(
            work_of(Path::new("root"), 315187, 903780),
            PathBuf::from("root/work/315187-903780")
        );
    }

    /// Lesson titles end in `.mp4` because that is the filename the API returns. Appending the
    /// container blind produced `01. 课程导言.mp4.mp4` — a file every player opens and no person
    /// recognises.
    #[test]
    fn a_title_that_already_names_a_container_does_not_get_a_second_one() {
        let path = output_of(Path::new("root"), &[], "01. 课程导言.mp4", "mp4");
        assert_eq!(path, PathBuf::from("root/out/01. 课程导言.mp4"));
        // A different container still replaces it, and the same extension is not doubled either.
        assert_eq!(leaf_name("课.mkv", "mp4"), "课.mp4");
        assert_eq!(leaf_name("课.mp4", "mp4"), "课.mp4");
        // A dot that is part of the title is not an extension.
        assert_eq!(leaf_name("1.2 浮点数", "mp4"), "1.2 浮点数.mp4");
        assert_eq!(leaf_name("无扩展名", "mkv"), "无扩展名.mkv");
        // A trailing dot is not a separator either, and a filename may not end in one anyway.
        assert_eq!(leaf_name("第 2 章 完.", "mp4"), "第 2 章 完.mp4");
    }

    /// The export root is the only directory the app writes below; a folder name from the course API
    /// must not be able to escape it. `..` survives every other rule in `sanitize`, because dots are
    /// legal in a name.
    #[test]
    fn a_folder_name_cannot_climb_out_of_the_export_root() {
        assert_eq!(sanitize(".."), "__");
        let hostile = vec!["..".to_string(), "..\\..\\Windows".to_string()];
        let path = output_of(Path::new("root"), &hostile, "x.mp4", "mp4");
        assert!(path.starts_with("root/out"), "{} escaped the root", path.display());
        assert!(
            !path.components().any(|part| part.as_os_str() == ".."),
            "{} still contains a parent reference",
            path.display()
        );
    }

    /// A title is attacker-adjacent data: it comes from the course API and lands in a path.
    #[test]
    fn illegal_filename_characters_are_replaced() {
        assert_eq!(sanitize(r#"1-1. 什么是<a/b>:"c"|d?*e"#), "1-1. 什么是_a_b___c__d__e");
        assert_eq!(sanitize("trailing dots..."), "trailing dots");
        assert_eq!(sanitize("   "), "video");
        assert_eq!(sanitize("CON"), "CON_");
        assert_eq!(sanitize("nul"), "nul_");
        assert_eq!(sanitize(&"长".repeat(300)).chars().count(), 120);
    }
}
