//! Running `evmedia catalog` and reading back what it wrote.
//!
//! The CLI already knows how to walk every authorized course, so a refresh is that process and
//! nothing else: this module starts it, streams its per-course progress to the log, and parses the
//! tree it leaves behind.

use crate::catalog::{self, Catalog};
use serde::{Deserialize, Serialize};
use std::process::Command;
use tauri::{AppHandle, Emitter, Runtime};

/// How long a catalog refresh may take before it is treated as hung. A 77-course account takes
/// minutes; anything approaching half an hour is a stuck request, not a slow one.
const REFRESH_TIMEOUT_SECS: u64 = 60 * 30;

#[derive(Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct RefreshOutcome {
    pub courses: usize,
    pub videos: usize,
    pub seconds: f64,
    pub catalog: Catalog,
}

/// The directory the CLI refreshed, if it has been refreshed at all.
pub fn catalog_at(root: &str) -> Result<Option<Catalog>, String> {
    let path = catalog::catalog_path(std::path::Path::new(root));
    if !path.is_file() {
        return Ok(None);
    }
    Catalog::load(&path).map(Some)
}

pub async fn run(
    app: AppHandle<impl Runtime>,
    cli: String,
    session: String,
    account: i64,
    root: String,
) -> Result<RefreshOutcome, String> {
    let started = std::time::Instant::now();
    let argv = vec![
        "catalog".to_string(),
        "--session".to_string(),
        session,
        "--account".to_string(),
        account.to_string(),
        "--output".to_string(),
        root.clone(),
    ];
    // The window never invents a command line: the CLI's own parser gets the last word.
    evmedia_contract::try_parse(&argv)?;

    let executable = cli.clone();
    let working = root.clone();
    crate::log::info(&format!("refreshing the catalog into {root} as account {account}"));
    let outcome =
        tauri::async_runtime::spawn_blocking(move || spawn_and_wait(&executable, &argv, &working))
            .await
            .map_err(|error| format!("目录刷新任务失败：{error}"))?;
    if let Err(error) = &outcome {
        crate::log::warn(&format!("catalog refresh failed: {error}"));
    }
    outcome?;

    let root_path = std::path::Path::new(&root);
    let catalog = Catalog::load(&catalog::catalog_path(root_path))?;
    let courses = count_courses(&catalog::index_path(root_path)).unwrap_or_default();
    crate::log::info(&format!(
        "catalog refreshed: {courses} courses, {} videos, {:.1}s",
        catalog.video_count(),
        started.elapsed().as_secs_f64()
    ));
    let _ = app.emit(
        crate::server::EVENT_CHANNEL,
        crate::server::Progress::Log {
            id: "catalog".to_string(),
            line: format!("目录已刷新：{} 门课程，{} 个视频", courses, catalog.video_count()),
        },
    );
    Ok(RefreshOutcome {
        courses,
        videos: catalog.video_count(),
        seconds: started.elapsed().as_secs_f64(),
        catalog,
    })
}

fn spawn_and_wait(cli: &str, argv: &[String], working: &str) -> Result<(), String> {
    let mut command = Command::new(cli);
    command.args(argv).current_dir(working);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let started = std::time::Instant::now();
    let mut child = command.spawn().map_err(|error| format!("启动 {cli} 失败：{error}"))?;

    // `catalog` reports one line per course on stderr; reading them is what turns a frozen window
    // during a long refresh into a process that is visibly working.
    if let Some(stderr) = child.stderr.take() {
        use std::io::{BufRead, BufReader};
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if started.elapsed().as_secs() > REFRESH_TIMEOUT_SECS {
                let _ = child.kill();
                return Err(format!("目录刷新超过 {REFRESH_TIMEOUT_SECS} 秒，已中止"));
            }
            crate::log::info(&line);
        }
    }
    let status = child.wait().map_err(|error| format!("等待目录进程失败：{error}"))?;
    if !status.success() {
        return Err(format!("目录刷新失败（exit {}）", status.code().unwrap_or(-1)));
    }
    Ok(())
}

/// How many courses `index.json` lists. Absent or unparseable is not an error: `catalog.json` is
/// what the window needs, and this is only the count it shows.
fn count_courses(index: &std::path::Path) -> Option<usize> {
    let bytes = std::fs::read(index).ok()?;
    let value: serde_json::Value = serde_json::from_slice(crate::strip_bom(&bytes)).ok()?;
    Some(value["courses"].as_object()?.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_catalog_is_not_an_error_but_a_broken_one_is() {
        let empty = std::env::temp_dir().join("evmedia-refresh-empty");
        let _ = std::fs::create_dir_all(&empty);
        assert!(catalog_at(&empty.display().to_string()).unwrap().is_none());

        let broken = empty.join("catalog.json");
        std::fs::write(&broken, "{ not json").unwrap();
        assert!(catalog_at(&empty.display().to_string()).is_err());
        let _ = std::fs::remove_dir_all(&empty);
    }

    #[test]
    fn a_course_count_survives_a_missing_index() {
        assert_eq!(count_courses(std::path::Path::new("no/such/index.json")), None);
    }
}
