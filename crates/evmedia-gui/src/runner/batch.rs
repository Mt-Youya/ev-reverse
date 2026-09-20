use crate::{
    job::{JobStage, JobStatus},
    plan::Options,
    protocol::Sink,
    queue::Queue,
    runner::KillSwitch,
};
use evmedia_contract::{ExportBatchArgs, ExportBatchPlan, ExportBatchVideo, ToArgv};
use std::{process::{Command, Stdio}, sync::Arc, thread, time::Duration};

/// Start exactly one CLI process for the entire selected batch. The plan file is the hand-off
/// boundary: the GUI supplies selection and paths; the CLI owns the global segment scheduler.
pub fn run(queue: &Queue, sink: &Arc<dyn Sink>, cli: &str, options: &Options, _workers: usize) {
    let (plan, ids) = {
        let state = queue.lock();
        let videos = state.items.iter().filter(|entry| entry.status == JobStatus::Queued).map(|entry| ExportBatchVideo {
            id: entry.item.id.clone(), course: entry.item.course, file: entry.item.file,
            output: entry.item.output.clone(), work: entry.item.work.clone(),
        }).collect::<Vec<_>>();
        let ids = state.items.iter().filter(|entry| entry.status == JobStatus::Queued).map(|entry| entry.item.id.clone()).collect::<Vec<_>>();
        (ExportBatchPlan { videos }, ids)
    };
    if ids.is_empty() { queue.settle(); sink.finished(); return; }
    let control = options.root.join("work");
    if let Err(error) = std::fs::create_dir_all(&control) { fail_all(queue, &ids, &error.to_string()); queue.settle(); sink.finished(); return; }
    let plan_path = control.join("global-segment-plan.json");
    if let Err(error) = std::fs::write(&plan_path, serde_json::to_vec_pretty(&plan).unwrap()) { fail_all(queue, &ids, &error.to_string()); queue.settle(); sink.finished(); return; }
    let stop_file = control.join(".evmedia-global-stop"); let _ = std::fs::remove_file(&stop_file);
    let argv = ExportBatchArgs { session: options.session.clone(), account: options.account, plan: plan_path,
        // Network concurrency is global and I/O-bound; leave CPU headroom for the decrypt and
        // encode queues even when the GUI's legacy per-video worker setting is one.
        download_jobs: options.jobs.max(8), decrypt_jobs: options.jobs.max(1).min(2), ffmpeg: options.ffmpeg.clone(), ffprobe: options.ffprobe.clone(), force: options.force }.to_argv();
    let mut command = Command::new(cli);
    command.args(&argv).arg("--json-events").arg("--stop-file").arg(&stop_file).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x0800_0000); }
    let mut child = match command.spawn() { Ok(child) => child, Err(error) => { fail_all(queue, &ids, &format!("启动全局队列失败：{error}")); queue.settle(); sink.finished(); return; } };
    let pid = child.id();
    for id in &ids { queue.patch(id, |entry| { entry.status = JobStatus::Running; entry.pid = Some(pid); entry.stage = Some(JobStage::Download); entry.message = "全局 segment 队列中".into(); }); }
    #[cfg(windows)] let kill = KillSwitch::new("__global__".into(), pid, None);
    #[cfg(not(windows))] let kill = KillSwitch::new("__global__".into(), pid);
    queue.lock().workers.push(kill);
    loop {
        if queue.lock().stop_requested { let _ = std::fs::write(&stop_file, b""); }
        match child.try_wait() {
            Ok(Some(status)) => {
                let stopped = queue.lock().stop_requested;
                for id in &ids { queue.patch(id, |entry| { entry.pid = None; if entry.item.output.exists() { entry.status = JobStatus::Complete; entry.stage = Some(JobStage::Done); entry.message = "已由全局 segment 队列导出".into(); } else if stopped { entry.status = JobStatus::Cancelled; entry.message = "已停止；已下载分段会续传".into(); } else { entry.status = JobStatus::Failed; entry.message = format!("全局队列退出：{status}"); } }); }
                break;
            }
            Ok(None) => thread::sleep(Duration::from_millis(200)),
            Err(error) => { fail_all(queue, &ids, &format!("查询全局队列失败：{error}")); break; }
        }
    }
    queue.forget_worker("__global__"); queue.settle(); sink.finished();
}

fn fail_all(queue: &Queue, ids: &[String], message: &str) { for id in ids { queue.patch(id, |entry| { entry.status = JobStatus::Failed; entry.message = message.to_string(); entry.pid = None; }); } }
