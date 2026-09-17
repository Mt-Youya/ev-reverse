// The export queue (right): one row per requested lesson, driven by the CLI's own events.
//
// A row's status is never guessed from a file on disk or an exit code. It is the status the CLI
// reported, because that is the only thing that knows whether a complete lesson was published —
// `partial` leaves a file that looks finished and is not.

// Phrases that mean the session itself has stopped working. The batch is stopped rather than allowed
// to fail two hundred times for one reason.
const AUTH_HINTS = ["其他设备上登录", "登录已过期", "unauthorized", "invalid token", "token expired"];

function looksLikeAuthFailure(text) {
  const value = (text ?? "").toLowerCase();
  return AUTH_HINTS.some((hint) => value.includes(hint.toLowerCase()));
}

function entryOf(id) {
  return state.queue.items.find((item) => item.item.id === id);
}

// One place where a queue movement becomes view state, whether it arrived as an event or as a
// snapshot taken at startup.
function onQueuePayload(message) {
  const payload = message.payload;
  if (payload.kind === "entry") {
    const incoming = payload.entry;
    const index = state.queue.items.findIndex((item) => item.item.id === incoming.item.id);
    if (index >= 0) state.queue.items[index] = incoming;
    else state.queue.items.push(incoming);
    renderQueueRow(incoming);
    syncButtons();
    if (incoming.status === "failed" && looksLikeAuthFailure(incoming.message)) {
      noteAuthFailure(incoming.message);
    }
    return;
  }
  if (payload.kind === "log") {
    appendLog(payload.id, payload.line);
    return;
  }
  if (payload.kind === "protocol") {
    applyProtocol(payload.id, payload.event);
    return;
  }
  if (payload.kind === "finished") {
    // The batch is over: re-read the queue instead of trusting a flag, so a run that ended while the
    // window was closing still settles into the right state.
    invoke("queue_snapshot")
      .then((snapshot) => {
        state.queue = snapshot;
        renderQueue();
        const counts = summarize(snapshot.items);
        if (counts.failed) {
          say(`本批结束：${counts.done} 个完成，${counts.failed} 个失败（可点“重试失败”）`, "warn");
        } else if (counts.done) {
          say(`本批结束：${counts.done} 个视频已导出`, "ok");
        }
      })
      .catch(fail);
  }
}

function summarize(items) {
  return {
    done: items.filter((item) => item.status === "complete").length,
    failed: items.filter((item) => item.status === "failed").length,
    skipped: items.filter((item) => item.status === "skipped").length,
  };
}

// The protocol events carry numbers the entry snapshot does not: a `progress` line arrives far more
// often than a status change, and it is what keeps the bar moving on a slow link.
function applyProtocol(id, event) {
  const row = entryOf(id);
  if (!row) return;
  if (event.event === "progress") {
    row.progress = row.progress ?? {};
    row.progress.done = event.done;
    row.progress.failed = event.failed;
    row.progress.elapsedSecs = event.elapsed_secs;
    if (event.segments > (row.progress.total ?? 0)) row.progress.total = event.segments;
    renderQueueRow(row);
  } else if (event.event === "artifact") {
    appendLog(id, `[产物] ${event.path}`);
  } else if (event.event === "finished") {
    appendLog(id, `[结束] ${event.status} ${event.message ?? ""}`);
  }
}

function noteAuthFailure(message) {
  if (state.queue.halt) return;
  state.queue.halt = message;
  invoke("set_halt", { message }).catch(() => {});
  say(`会话可能已失效：${message} — 请重新登录 EVPlayer2，点“自动获取”刷新会话，再点“重试失败”`, "bad");
}

function statusLabel(entry) {
  const names = {
    queued: "排队",
    running: "导出中",
    complete: "完成",
    failed: "失败",
    cancelled: "已取消",
    skipped: "已跳过",
  };
  return names[entry.status] ?? entry.status;
}

function stageLabel(stage) {
  const names = { preparing: "准备", download: "下载", decrypt: "解密", remux: "合成", done: "完成" };
  return names[stage] ?? "";
}

function appendLog(id, line) {
  const lines = state.logs.get(id) ?? [];
  lines.push(line);
  if (lines.length > 400) lines.splice(0, lines.length - 400);
  state.logs.set(id, lines);

  const node = document.querySelector(`[data-log="${CSS.escape(id)}"]`);
  if (!node) return;
  const atBottom = node.scrollTop + node.clientHeight >= node.scrollHeight - 20;
  node.textContent += (node.textContent ? "\n" : "") + line;
  if (atBottom) node.scrollTop = node.scrollHeight;
}

function progressPercent(entry) {
  if (entry.status === "complete") return 100;
  const total = entry.progress?.total ?? 0;
  if (!total) return entry.status === "running" ? 4 : 0;
  return Math.min(100, Math.round(((entry.progress?.done ?? 0) / total) * 100));
}

function renderQueue() {
  const host = el("queue");
  host.replaceChildren();
  if (!state.queue.items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "队列是空的。在左边选课，在中间勾选视频，然后点“开始导出”。";
    host.append(empty);
    updateOverall();
    syncButtons();
    return;
  }
  for (const entry of state.queue.items) host.append(queueRow(entry));
  updateOverall();
  syncButtons();
}

function renderQueueRow(entry) {
  const existing = document.querySelector(`[data-row="${CSS.escape(entry.item.id)}"]`);
  if (!existing) {
    renderQueue();
    return;
  }
  existing.replaceWith(queueRow(entry));
  updateOverall();
  syncButtons();
}

function queueRow(entry) {
  const row = document.createElement("div");
  row.className = `queue-row ${entry.status}`;
  row.dataset.row = entry.item.id;

  const top = document.createElement("div");
  top.className = "queue-top";

  const status = document.createElement("span");
  status.className = `status status-${entry.status}`;
  status.textContent = statusLabel(entry);

  const title = document.createElement("span");
  title.className = "queue-title";
  title.textContent = entry.item.title || entry.item.id;
  title.title = `${entry.item.path.join(" / ")}\n${entry.item.id}\n输出：${entry.item.output}`;

  const counts = document.createElement("span");
  counts.className = "queue-counts";
  const total = entry.progress?.total ?? 0;
  counts.textContent = total ? `${entry.progress.done}/${total} 段` : "";

  const actions = document.createElement("span");
  actions.className = "queue-actions";
  if (entry.logPath) {
    const transcript = document.createElement("button");
    transcript.type = "button";
    transcript.className = "link";
    transcript.textContent = "完整日志";
    transcript.title = entry.logPath;
    transcript.addEventListener("click", () => invoke("open_path", { path: entry.logPath }));
    actions.append(transcript);
  }
  if (["failed", "complete", "skipped"].includes(entry.status)) {
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "link";
    copy.textContent = "命令";
    copy.title = "复制这一节的命令行，便于手工重跑";
    copy.addEventListener("click", async () => {
      await navigator.clipboard.writeText(entry.argv.join(" "));
      copy.textContent = "已复制";
      setTimeout(() => (copy.textContent = "命令"), 1200);
    });
    actions.append(copy);
  }
  if (entry.status === "failed") {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "link";
    open.textContent = "目录";
    open.title = "打开这一节的临时目录，里面有已下载的分段和日志";
    open.addEventListener("click", () => invoke("open_path", { path: entry.item.work }));
    actions.append(open);
  }

  top.append(status, title, counts, actions);
  row.append(top);

  const bar = document.createElement("div");
  bar.className = "bar thin";
  const fill = document.createElement("div");
  fill.className = "bar-fill";
  fill.style.width = `${progressPercent(entry)}%`;
  bar.append(fill);
  row.append(bar);

  const message = document.createElement("p");
  message.className = "queue-message";
  message.textContent = entry.message || (entry.status === "running" ? "正在准备…" : "");
  if (entry.stage) {
    const stage = document.createElement("span");
    stage.className = "stage";
    stage.textContent = stageLabel(entry.stage);
    message.prepend(stage);
  }
  row.append(message);

  const details = document.createElement("details");
  details.className = "queue-details";
  const summary = document.createElement("summary");
  // The panel is capped; the file is not. Saying so is the difference between "the log is short"
  // and "the part that explains this failure was dropped".
  summary.textContent = entry.logPath ? "日志（最近 400 行，完整记录见“完整日志”）" : "日志";
  const pre = document.createElement("pre");
  pre.className = "log";
  pre.dataset.log = entry.item.id;
  pre.textContent = (state.logs.get(entry.item.id) ?? []).join("\n");
  details.append(summary, pre);
  row.append(details);

  return row;
}

function updateOverall() {
  const items = state.queue.items;
  if (!items.length) {
    el("overall-label").textContent = "尚未开始";
    el("overall-counts").textContent = "";
    el("overall-fill").style.width = "0%";
    return;
  }
  const counts = summarize(items);
  const running = items.filter((item) => item.status === "running").length;
  const finished = counts.done + counts.failed + counts.skipped;

  el("overall-label").textContent = state.queue.running
    ? `正在导出 ${running} 个，已完成 ${counts.done}/${items.length}`
    : `本批 ${items.length} 个视频`;
  el("overall-counts").textContent =
    `${counts.done} 完成` +
    (counts.failed ? ` · ${counts.failed} 失败` : "") +
    (counts.skipped ? ` · ${counts.skipped} 跳过` : "");
  el("overall-fill").style.width = `${Math.round((finished / items.length) * 100)}%`;
  el("overall-fill").classList.toggle("warn", counts.failed > 0);
}

function syncButtons() {
  const running = state.queue.running || state.queue.items.some((item) => item.status === "running");
  const stopping = Boolean(state.queue.stopping);
  const ready = Boolean(state.cli?.ok);
  const selected = state.selection.size;

  el("run").disabled = !ready || running || selected === 0;
  el("run").textContent = running ? "导出中…" : selected ? `开始导出（${selected}）` : "开始导出";
  el("stop").disabled = !running || stopping;
  el("stop").textContent = stopping ? "正在停止…" : "停止";
  // Only offered once a stop has been asked for: the graceful stop is the one that leaves a
  // resumable lesson behind.
  el("force").disabled = !stopping;
  el("retry").disabled = running || !state.queue.items.length;
  el("refresh").disabled = running || !ready;
  el("open-root").disabled = !el("root").value.trim();
}
