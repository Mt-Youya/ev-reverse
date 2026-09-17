//! Getting the session file without asking the user where it is.
//!
//! The session is a short-lived credential that exists in exactly two places: inside the running
//! player, and in whatever file a capture wrote it to. Asking a user to find a path is asking them
//! to do the tool's job — so this module reads it out of the player, in one click, using the probe
//! that was already written to do it (`tools/parser-tools/probe_catalog_constants.py`).
//!
//! The probe is not reimplemented here. It holds the byte offsets, checked against a known build of
//! `EVPlayer2.exe`, and duplicating that would be duplicating the part most likely to be wrong.

use serde::{Deserialize, Serialize};
use std::{
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant},
};

/// The probe is a one-shot attach; if it has not answered in this long, something is wrong with the
/// environment rather than with the player, and saying so beats hanging the button.
const PROBE_TIMEOUT: Duration = Duration::from_secs(90);

/// Where the probe script lives, relative to the workspace root.
const PROBE: &str = "tools/parser-tools/probe_catalog_constants.py";

/// Interpreters that have been used for this project, most specific first. The probe needs `frida`,
/// which is not in a bare Python, so a candidate is only accepted if it can import it.
fn python_candidates() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(value) = std::env::var_os("EVMEDIA_PYTHON") {
        out.push(PathBuf::from(value));
    }
    if let Some(home) = std::env::var_os("USERPROFILE") {
        let home = PathBuf::from(home);
        out.push(home.join(".conda").join("envs").join("subgen").join("python.exe"));
        out.push(home.join("miniconda3").join("python.exe"));
        out.push(home.join("anaconda3").join("python.exe"));
    }
    for directory in ["C:\\Python313", "C:\\Python312", "C:\\Python311"] {
        out.push(PathBuf::from(directory).join("python.exe"));
    }
    out.push(PathBuf::from("python"));
    out.push(PathBuf::from("py"));
    out
}

/// The first interpreter that exists and can `import frida`. Reported rather than hidden: a missing
/// Frida is the one reason this feature cannot work, and the user has to install it.
pub fn find_python() -> Result<PathBuf, String> {
    let mut tried = Vec::new();
    for candidate in python_candidates() {
        if candidate.components().count() > 1 && !candidate.is_file() {
            continue;
        }
        let ok = Command::new(&candidate)
            .args(["-c", "import frida"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
        if ok {
            return Ok(candidate);
        }
        tried.push(candidate.display().to_string());
    }
    Err(format!(
        "找不到带 frida 的 Python。装一个再试：pip install frida（试过：{}）",
        tried.join("、")
    ))
}

/// The probe script, found by walking up from this executable so a workspace build and an installed
/// build both work. An explicit `EVMEDIA_PROBE` wins.
pub fn find_probe() -> Result<PathBuf, String> {
    if let Some(value) = std::env::var_os("EVMEDIA_PROBE") {
        let path = PathBuf::from(value);
        if path.is_file() {
            return Ok(path);
        }
        return Err(format!("EVMEDIA_PROBE 指向的文件不存在：{}", path.display()));
    }
    let mut roots = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(directory) = exe.parent() {
            roots.push(directory.to_path_buf());
            // target/release -> the workspace root is two levels up.
            if let Some(up) = directory.parent().and_then(Path::parent) {
                roots.push(up.to_path_buf());
                if let Some(more) = up.parent() {
                    roots.push(more.to_path_buf());
                }
            }
        }
    }
    if let Ok(current) = std::env::current_dir() {
        roots.push(current);
    }
    for root in roots {
        let candidate = root.join(PROBE);
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    Err(format!("找不到探针脚本 {PROBE}，可用 EVMEDIA_PROBE 指定它的位置"))
}

/// The running player, by window rather than by name: `EVPlayer2.exe` is a small launcher and the
/// heavyweight process is a second one, and the probe needs the one that holds the session.
pub fn find_player() -> Result<u32, String> {
    #[cfg(windows)]
    {
        use windows_sys::Win32::{
            Foundation::{BOOL, HWND, LPARAM},
            UI::WindowsAndMessaging::{EnumWindows, GetWindowThreadProcessId, IsWindowVisible},
        };
        struct Found {
            pid: u32,
            area: i64,
        }
        unsafe extern "system" fn visit(window: HWND, param: LPARAM) -> BOOL {
            let found = &mut *(param as *mut Found);
            if IsWindowVisible(window) == 0 {
                return 1;
            }
            let mut pid = 0u32;
            GetWindowThreadProcessId(window, &mut pid);
            if pid == 0 {
                return 1;
            }
            if !process_name(pid).eq_ignore_ascii_case("EVPlayer2.exe") {
                return 1;
            }
            // The rendering process is the one with a window; prefer the largest if there are two.
            let mut rect: windows_sys::Win32::Foundation::RECT = unsafe { std::mem::zeroed() };
            let ok = windows_sys::Win32::UI::WindowsAndMessaging::GetClientRect(window, &mut rect);
            let area = if ok != 0 {
                i64::from(rect.right - rect.left) * i64::from(rect.bottom - rect.top)
            } else {
                0
            };
            if area >= found.area {
                *found = Found { pid, area };
            }
            1
        }
        let mut found = Found { pid: 0, area: -1 };
        unsafe { EnumWindows(Some(visit), &mut found as *mut Found as LPARAM) };
        if found.pid == 0 {
            Err("没有找到正在运行的 EVPlayer2 窗口，先打开播放器并登录".to_string())
        } else {
            Ok(found.pid)
        }
    }
    #[cfg(not(windows))]
    {
        Err("读取播放器会话只在 Windows 上可用".to_string())
    }
}

#[cfg(windows)]
fn process_name(pid: u32) -> String {
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        System::Threading::{
            OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
        },
    };
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle == 0 {
            return String::new();
        }
        let mut buffer = [0u16; 512];
        let mut length = buffer.len() as u32;
        let ok = QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut length);
        CloseHandle(handle);
        if ok == 0 {
            return String::new();
        }
        let full = String::from_utf16_lossy(&buffer[..length as usize]);
        full.rsplit(['\\', '/']).next().unwrap_or("").to_string()
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Sniffed {
    /// Where the credentials were written, so the window can show the real path it will pass on.
    pub path: String,
    pub pid: u32,
    pub python: String,
    /// Which of the five fields came back. Names only — the values are credentials.
    pub fields: Vec<String>,
    /// What the probe itself printed. It reports the credentials as omitted and says which function
    /// it hooked, which is how a user can tell a stale probe from a broken one.
    pub probe_output: String,
    pub seconds: f64,
}

/// Read the session from the running player and write it next to the export root.
///
/// Retried, because attaching to a process is not reliably instantaneous: Frida answers
/// `PermissionDeniedError: unable to access process … from the current user account` for a moment
/// while a target is busy, and the same call succeeds a second later. Reporting that as "your
/// player is not logged in" would be wrong, and asking the user to press the button again is asking
/// them to do the retry.
pub fn sniff(root: &Path) -> Result<Sniffed, String> {
    const ATTEMPTS: usize = 4;
    let mut last = String::new();
    for attempt in 0..ATTEMPTS {
        match sniff_once(root) {
            Ok(sniffed) => return Ok(sniffed),
            Err(error) => {
                last = error;
                if attempt + 1 < ATTEMPTS {
                    std::thread::sleep(Duration::from_millis(1200));
                }
            }
        }
    }
    Err(last)
}

fn sniff_once(root: &Path) -> Result<Sniffed, String> {
    let started = Instant::now();
    let python = find_python()?;
    let probe = find_probe()?;
    let pid = find_player()?;

    std::fs::create_dir_all(root).map_err(|error| format!("无法创建 {}：{error}", root.display()))?;
    let path = root.join("session.json");

    let mut child = Command::new(&python)
        .arg("-u")
        .arg(&probe)
        .arg("--pid")
        .arg(pid.to_string())
        .arg("--session-out")
        .arg(&path)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("启动 Python 失败（{}）：{error}", python.display()))?;

    let out = drain(child.stdout.take());
    let err = drain(child.stderr.take());

    let deadline = Instant::now() + PROBE_TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => {
                if Instant::now() > deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                std::thread::sleep(Duration::from_millis(150));
            }
            Err(error) => return Err(format!("等待探针失败：{error}")),
        }
    };

    let stdout = out.join().unwrap_or_default();
    let stderr = err.join().unwrap_or_default();
    let Some(status) = status else {
        return Err(format!(
            "探针超过 {} 秒没有返回，已中止。{}",
            PROBE_TIMEOUT.as_secs(),
            tail(&stderr)
        ));
    };
    if !status.success() || !path.is_file() {
        return Err(format!(
            "探针失败（exit {}）：{}。请确认播放器已经登录，且探针脚本与播放器版本匹配。",
            status.code().unwrap_or(-1),
            tail(&stderr)
        ));
    }
    // Read it back through the same validator the manual path uses, so a probe that wrote something
    // unusable is caught here rather than by a batch of two hundred failures.
    let summary = crate::session::inspect(&path)?;
    Ok(Sniffed {
        path: path.display().to_string(),
        pid,
        python: python.display().to_string(),
        fields: field_names(&summary),
        probe_output: tail(&stdout),
        seconds: started.elapsed().as_secs_f64(),
    })
}

fn field_names(summary: &crate::session::Summary) -> Vec<String> {
    let mut names = vec!["token".to_string(), "data_key".to_string(), "sign_secret".to_string()];
    if !summary.machine_id.is_empty() {
        names.push("machine_id".to_string());
    }
    if summary.busi_id.is_some() {
        names.push("busi_id".to_string());
    }
    let _ = &summary.token_hint;
    names
}

/// Read a pipe to the end on its own thread, so waiting on the process cannot deadlock against a
/// full pipe buffer — which is exactly what a probe that prints a lot while failing would do.
fn drain<R: Read + Send + 'static>(mut pipe: Option<R>) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut text = String::new();
        if let Some(pipe) = pipe.as_mut() {
            let _ = pipe.read_to_string(&mut text);
        }
        text
    })
}

/// The last few lines of the probe's own diagnostics — it reports unsupported builds there, and that
/// message is the whole value of a failure.
fn tail(text: &str) -> String {
    let lines: Vec<&str> = text.lines().filter(|line| !line.trim().is_empty()).collect();
    lines[lines.len().saturating_sub(3)..].join(" / ")
}

/// Whether the two media binaries the CLI shells out to can be started at all. Their names alone are
/// what the CLI is given, so this is a check of `PATH`, not of a file.
pub fn media_tools_ok(ffmpeg: &str, ffprobe: &str) -> bool {
    [ffmpeg, ffprobe].iter().all(|program| {
        Command::new(program)
            .arg("-version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_tail_of_a_failure_is_the_useful_part() {
        let text = "line one\n\nline two\nline three\nline four\n";
        assert_eq!(tail(text), "line two / line three / line four");
        assert_eq!(tail(""), "");
    }

    /// The probe path is resolved relative to this build, so the test asserts the shape of the
    /// search rather than a fixed directory.
    #[test]
    fn a_probe_path_is_either_found_or_explained() {
        match find_probe() {
            Ok(path) => assert!(path.is_file() && path.ends_with("probe_catalog_constants.py")),
            Err(error) => assert!(error.contains("EVMEDIA_PROBE")),
        }
    }

    #[test]
    fn a_missing_interpreter_is_reported_with_the_reason() {
        // Nothing here asserts that Frida is installed — only that the failure says what to do.
        if let Err(error) = find_python() {
            assert!(error.contains("frida"), "unhelpful message: {error}");
        }
    }

    /// Reports rather than asserts: whether a player is running is a fact about the machine, not
    /// about the code. Run it with `--nocapture` when the window claims the player is missing.
    #[test]
    fn report_what_the_environment_looks_like() {
        println!("player: {:?}", find_player());
        println!("python: {:?}", find_python());
        println!("probe: {:?}", find_probe());
        println!("ffmpeg/ffprobe: {}", media_tools_ok("ffmpeg", "ffprobe"));
    }
}
