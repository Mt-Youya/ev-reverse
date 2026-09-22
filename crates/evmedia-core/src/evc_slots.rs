//! Cross-process capacity control for EVC CPU-decoder/NVENC repair jobs.

use anyhow::{bail, Context, Result};
use evmedia_contract::Reporter;
use std::{fs, io, path::Path};

/// The GUI supplies its publish-pool size per child. Direct CLI use stays conservative.
pub(crate) fn slots() -> usize {
    std::env::var("EVMEDIA_EVC_SLOTS")
        .ok()
        .and_then(|value| value.parse().ok())
        .filter(|value: &usize| (1..=8).contains(value))
        .unwrap_or(1)
}

/// Keep four concurrent EVC repairs from giving every custom CPU decoder four cores.
pub(crate) fn decode_threads(slots: usize) -> usize {
    std::thread::available_parallelism()
        .map(|count| count.get() / slots.max(1))
        .unwrap_or(1)
        .clamp(1, 4)
}

/// Context probing runs short single-thread decoder processes, so it must shrink with capacity.
pub(crate) fn probe_workers(slots: usize) -> usize {
    (4 / slots.max(1)).max(1)
}

/// One of a bounded number of repair leases shared by GUI-spawned CLI processes.
pub(crate) struct EvcLease {
    _file: fs::File,
    #[cfg(not(windows))]
    path: std::path::PathBuf,
}

impl EvcLease {
    fn try_acquire(work: &Path, slots: usize) -> io::Result<Option<Self>> {
        let parent = work.parent().unwrap_or(work);
        for slot in 0..slots.max(1) {
            let path = parent.join(format!(".evmedia-evc-repair-{slot}.lock"));
            #[cfg(windows)]
            {
                use std::os::windows::fs::OpenOptionsExt;
                let mut options = fs::OpenOptions::new();
                options.write(true).create(true).truncate(true).share_mode(0);
                match options.open(&path) {
                    Ok(mut file) => {
                        use std::io::Write;
                        let _ = write!(file, "{}", std::process::id());
                        return Ok(Some(Self { _file: file }));
                    }
                    Err(error) if slot_busy(&error) => continue,
                    Err(error) => return Err(error),
                }
            }
            #[cfg(not(windows))]
            match fs::OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(file) => return Ok(Some(Self { _file: file, path })),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
        Ok(None)
    }

    pub(crate) fn acquire(work: &Path, slots: usize, reporter: &Reporter) -> Result<Self> {
        media_wait(reporter, slots);
        loop {
            if reporter.stopped() {
                bail!("export stopped");
            }
            match Self::try_acquire(work, slots) {
                Ok(Some(lease)) => return Ok(lease),
                Ok(None) => reporter.sleep(std::time::Duration::from_millis(500)),
                Err(error) => return Err(error).context("acquire EVC conversion slot"),
            }
        }
    }
}

fn slot_busy(error: &io::Error) -> bool {
    error.kind() == io::ErrorKind::PermissionDenied
        || (cfg!(windows) && matches!(error.raw_os_error(), Some(32 | 33)))
}

#[cfg(not(windows))]
impl Drop for EvcLease {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

fn media_wait(reporter: &Reporter, slots: usize) {
    reporter.info(format!("正在等待兼容转换槽位（最多 {slots} 个并行），尚未生成成品"));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn two_repair_leases_can_coexist_when_two_slots_are_requested() {
        let root = std::env::temp_dir().join(format!("evmedia-evc-slots-{}", std::process::id()));
        let work = root.join("work");
        fs::create_dir_all(&work).unwrap();
        let first = EvcLease::try_acquire(&work, 2).unwrap();
        let second = EvcLease::try_acquire(&work, 2).unwrap();
        let third = EvcLease::try_acquire(&work, 2).unwrap();
        assert!(first.is_some());
        assert!(second.is_some());
        assert!(third.is_none());
        drop(third);
        drop(second);
        drop(first);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn decoder_thread_budget_is_divided_across_slots() {
        assert_eq!(probe_workers(1), 4);
        assert_eq!(probe_workers(4), 1);
        assert!((1..=4).contains(&decode_threads(4)));
    }
}
