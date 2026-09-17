// The window drives `evmedia.exe`; it does not reimplement it.
//
// Everything on this side is presentation and orchestration: the course tree comes from the catalog
// the CLI wrote, a queue row becomes one `evmedia export-evs` argv built by the Rust side, and every
// number in the progress bars came out of that process's own JSON event stream. Nothing here knows
// what a segment key is, and nothing here decrypts anything.

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const el = (id) => document.getElementById(id);

// Everything the view renders from. One object, replaced field by field, so there is exactly one
// answer to "what is on screen right now".
const state = {
  cli: null,
  // What `check_environment` reports: the CLI, the session, the media tools, the player.
  environment: null,
  settings: {
    cliPath: null,
    session: "",
    account: 0,
    root: "",
    workers: 4,
    jobs: 8,
    extension: "mp4",
    ffmpeg: "ffmpeg",
    ffprobe: "ffprobe",
    force: false,
    lastSelected: [],
  },
  catalog: null,
  // Flat list of every video in the tree, in tree order.
  videos: [],
  // A Set would serialise to `{}` through `invoke`, so the wire type is an array.
  selection: new Set(),
  queue: { items: [], running: false, halt: null },
  logs: new Map(),
  expanded: new Set(),
  filter: "",
};

// ------------------------------------------------------------------ errors

// One place where a failure becomes something the user can read and act on. The Rust side already
// writes its errors in Chinese and names the missing thing, so this only has to put them on screen
// instead of in the console.
function fail(error) {
  const text = typeof error === "string" ? error : (error?.message ?? String(error));
  const banner = el("banner");
  banner.textContent = text;
  banner.className = "banner bad";
  console.error(error);
}

function clearBanner() {
  el("banner").className = "banner hidden";
}

function say(text, kind = "ok") {
  const banner = el("banner");
  banner.textContent = text;
  banner.className = `banner ${kind}`;
}

// --------------------------------------------------------------- settings

function readSettings() {
  state.settings = {
    cliPath: state.cli?.path ?? null,
    session: el("session").value.trim(),
    account: Number(el("account").value) || 0,
    root: el("root").value.trim(),
    workers: Number(el("workers").value) || 4,
    jobs: Number(el("jobs").value) || 8,
    extension: el("extension").value,
    ffmpeg: el("ffmpeg").value.trim() || "ffmpeg",
    ffprobe: el("ffprobe").value.trim() || "ffprobe",
    force: el("force").checked,
    lastSelected: [...state.selection],
  };
  return state.settings;
}

function fillSettings(settings) {
  state.settings = { ...state.settings, ...settings };
  const fields = {
    session: settings.session,
    account: settings.account || "",
    root: settings.root,
    workers: settings.workers,
    jobs: settings.jobs,
    extension: settings.extension,
    ffmpeg: settings.ffmpeg,
    ffprobe: settings.ffprobe,
    force: settings.force,
  };
  for (const [id, value] of Object.entries(fields)) {
    const node = el(id);
    if (node.type === "checkbox") node.checked = Boolean(value);
    else node.value = value ?? "";
  }
  state.settings.lastSelected = settings.lastSelected ?? [];
}

async function saveSettings() {
  try {
    await invoke("set_config", { settings: readSettings() });
    say("设置已保存", "ok");
    setTimeout(clearBanner, 1500);
  } catch (error) {
    fail(error);
  }
}

// ------------------------------------------------------------- environment

// One call that answers "can this window do anything yet", so the window shows the one thing that is
// missing instead of discovering it at the first button press.
async function checkEnvironment() {
  try {
    const environment = await invoke("check_environment", { settings: readSettings() });
    state.environment = environment;
    state.cli = environment.cli;
    paintEnvironment();
    syncButtons();
  } catch (error) {
    fail(error);
  }
}

function paintEnvironment() {
  const chip = el("cli-status");
  const status = state.environment?.cli ?? state.cli;
  if (!status) {
    chip.className = "chip chip-warn";
    chip.textContent = "正在查找 evmedia…";
    return;
  }
  chip.className = "chip " + (status.ok ? "chip-ok" : "chip-bad");
  chip.textContent = status.ok
    ? `${String(status.version).replace(/^evmedia\s*/, "v")} · ${status.source}`
    : "找不到 evmedia";
  chip.title = status.detail || status.path || "";

  const environment = state.environment;
  if (!environment) return;
  const hint = el("session-hint");
  if (environment.session) {
    hint.className = "hint";
    hint.textContent =
      `会话就绪：token ${environment.session.tokenHint}` +
      (environment.session.accountId ? ` · 账号 ${environment.session.accountId}` : "");
  } else if (environment.sessionError) {
    hint.className = "hint bad";
    hint.textContent = environment.sessionError;
  } else if (environment.playerRunning) {
    hint.className = "hint";
    hint.textContent = "还没有会话：点“自动获取”，从正在运行的 EVPlayer2 里读一份";
  } else {
    hint.className = "hint";
    hint.textContent = "还没有会话：先打开 EVPlayer2 并登录，再点“自动获取”";
  }
  if (!environment.mediaTools) {
    hint.className = "hint bad";
    hint.textContent += " · 找不到 ffmpeg/ffprobe，请装好并加入 PATH";
  }
  // One line of diagnostics on the hint's tooltip, so "the button does nothing" comes with the
  // checks the app actually made rather than a guess about them.
  hint.title = JSON.stringify({
    player: environment.playerRunning,
    media: environment.mediaTools,
    sessionPath: environment.session?.path ?? null,
    note: environment.note ?? null,
  });
}

// Read the session out of the running player, in one click. This is the step that used to be "go
// find the file the probe wrote"; the probe is still what reads it, but nothing about where it lives
// is the user's problem any more.
async function sniffSession({ quiet = false } = {}) {
  const button = el("sniff");
  const settings = readSettings();
  if (!settings.root) return fail("先选择导出目录，会话文件会写在它下面");
  button.disabled = true;
  button.textContent = "读取中…";
  if (!quiet) say("正在从 EVPlayer2 读取会话；播放器需要已经登录", "warn");
  try {
    const sniffed = await invoke("sniff_session", { settings });
    // The window now knows exactly where the session is, so it stops guessing.
    el("session").value = sniffed.path;
    await saveSettings();
    await checkEnvironment();
    if (!sniffed.fields.length) {
      say(`会话已写入 ${sniffed.path}，但字段不完整；请确认播放器已经登录`, "bad");
    } else {
      say(
        `会话已写入 ${sniffed.path}（进程 ${sniffed.pid}，${sniffed.fields.join("、")}，` +
          `${sniffed.seconds.toFixed(1)} 秒）`,
        "ok"
      );
    }
  } catch (error) {
    fail(error);
  } finally {
    button.disabled = false;
    button.textContent = "自动获取";
    syncButtons();
  }
}

// --------------------------------------------------------------- catalog

async function refreshCatalog() {
  if (state.queue.running) return fail("正在导出，先停止再刷新目录");
  const settings = readSettings();
  if (!settings.account) return fail("先填写账号 ID");
  if (!settings.root) return fail("先选择导出目录");
  // The path the backend resolved, not the text box: an empty box means "the session the app wrote
  // beside the export root", and the CLI needs the real path.
  const session = state.environment?.session?.path;
  if (!session) return fail("还没有会话文件：点“自动获取”");
  await invoke("log_note", { message: `refresh requested: account=${settings.account}` }).catch(() => {});

  el("refresh").disabled = true;
  el("refresh").textContent = "刷新中…";
  say("正在读取课程目录；课程多时这一步要几分钟", "warn");
  try {
    const outcome = await invoke("refresh_catalog", {
      cli: state.cli.path,
      session,
      account: settings.account,
      root: settings.root,
    });
    state.catalog = outcome.catalog;
    state.expanded = new Set();
    await saveSettings();
    buildTree();
    say(
      `目录已刷新：${outcome.courses} 门课程，${outcome.videos} 个视频（${outcome.seconds.toFixed(1)} 秒）`,
      "ok"
    );
  } catch (error) {
    fail(error);
  } finally {
    el("refresh").disabled = false;
    el("refresh").textContent = "刷新目录";
    syncButtons();
  }
}

async function loadCatalog() {
  const root = el("root").value.trim();
  if (!root) return;
  try {
    state.catalog = await invoke("load_catalog", { root });
    state.expanded = new Set();
    buildTree();
  } catch (error) {
    fail(error);
  }
}

// ------------------------------------------------------------------- run

function selectedIds() {
  return state.videos.filter((video) => state.selection.has(video.id)).map((video) => video.id);
}

// Ask the Rust side what this batch would do — which argv, which output path, which rows it would
// skip — and put that on screen before anything is spawned.
async function planExport(ids) {
  const rows = await invoke("preview_export", { settings: readSettings(), ids });
  state.queue = { items: rows, running: false, halt: null };
  state.logs = new Map();
  renderQueue();
  return rows;
}

async function runExport() {
  const ids = selectedIds();
  await invoke("log_note", { message: `run pressed with ${ids.length} selected` }).catch(() => {});
  if (!ids.length) return fail("先在中间一列选中要导出的视频");
  if (!state.cli?.ok) return fail("找不到 evmedia CLI，先构建 CLI 或在设置里指定路径");
  if (state.queue.running) return fail("已经在导出中");

  clearBanner();
  try {
    if (!state.queue.items.length) await planExport(ids);
    await invoke("enqueue_export", { settings: readSettings(), ids });
    await invoke("start_export", { settings: readSettings() });
    say("已开始导出；再打开这个窗口时点“重试失败”可以继续没做完的视频", "warn");
    syncButtons();
  } catch (error) {
    fail(error);
  }
}

async function stopExport() {
  try {
    await invoke("stop_export");
    state.queue.stopping = true;
    syncButtons();
    say(
      "已请求停止：CLI 会等当前分段下完、合并已下载的内容再退出。" +
        "如果这一节正在下载很多分段，可能要等一会儿——等不及就点“强制结束”。",
      "warn"
    );
  } catch (error) {
    fail(error);
  }
}

/// Stop now. The graceful stop is the one that leaves a resumable lesson behind, so this is the
/// second button, not the first — but it exists, because a user who has waited long enough should
/// not have to open Task Manager.
async function forceStop() {
  try {
    const killed = await invoke("force_stop");
    state.queue.stopping = true;
    syncButtons();
    say(
      `已强制结束 ${killed} 个导出进程（连它启动的 ffmpeg 一起）。` +
        "已下载的分段都留着，重跑会复用它们。",
      "bad"
    );
  } catch (error) {
    fail(error);
  }
}

async function retryFinished() {
  try {
    const count = await invoke("retry_finished");
    if (!count) return say("没有可重试的行", "warn");
    state.logs = new Map();
    renderQueue();
    await invoke("start_export", { settings: readSettings() });
    say(`已重新排队 ${count} 个视频`, "warn");
  } catch (error) {
    fail(error);
  }
}

async function clearFinished() {
  try {
    await invoke("clear_finished");
    state.queue = await invoke("queue_snapshot");
    state.logs = new Map();
    renderQueue();
    syncButtons();
  } catch (error) {
    fail(error);
  }
}

/// A window that was closed mid-batch and reopened finds the queue still running. The snapshot is
/// the truth; this only has to show it, including a stop that is already in progress.
async function refreshQueueSnapshot() {
  state.queue = await invoke("queue_snapshot");
  renderQueue();
  syncButtons();
}

async function openRoot() {
  const root = el("root").value.trim();
  if (root) await invoke("open_path", { path: root });
}

// -------------------------------------------------------------- selection

function toggleSelection(id, on) {
  if (on) state.selection.add(id);
  else state.selection.delete(id);
  syncButtons();
  updateSelectionSummary();
}

function setSelectionForFolder(node, on) {
  for (const id of videoIdsOf(node)) {
    if (on) state.selection.add(id);
    else state.selection.delete(id);
  }
  renderVideos();
  renderTree();
  syncButtons();
}

// ------------------------------------------------------------------ boot

async function boot() {
  const settings = await invoke("get_config");
  fillSettings(settings);

  await listen("queue", onQueuePayload);
  await refreshQueueSnapshot();

  for (const field of [
    "session",
    "account",
    "root",
    "workers",
    "jobs",
    "extension",
    "ffmpeg",
    "ffprobe",
    "force",
  ]) {
    el(field).addEventListener("change", () => {
      readSettings();
      checkEnvironment();
      syncButtons();
    });
  }
  el("save").addEventListener("click", saveSettings);
  el("refresh").addEventListener("click", refreshCatalog);
  el("sniff").addEventListener("click", () => sniffSession());
  el("run").addEventListener("click", runExport);
  el("stop").addEventListener("click", stopExport);
  el("force-stop").addEventListener("click", forceStop);
  el("retry").addEventListener("click", retryFinished);
  el("clear").addEventListener("click", clearFinished);
  el("open-root").addEventListener("click", openRoot);

  document.querySelectorAll("[data-pick]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = button.dataset.pick;
      const current = el(target).value.trim() || null;
      // A media binary is a file; the export root is a directory. Everything else follows from
      // which one the button says it picks.
      const chosen = button.dataset.file
        ? await invoke("pick_file", { current, filter: button.dataset.file })
        : await invoke("pick_directory", { current });
      if (!chosen) return;
      el(target).value = chosen;
      readSettings();
      if (target === "root") {
        // A new root has a different catalog, or none at all.
        state.catalog = null;
        buildTree();
        await saveSettings();
        await checkEnvironment();
        await loadCatalog();
      }
      syncButtons();
    });
  });

  el("filter").addEventListener("input", () => {
    state.filter = el("filter").value.trim().toLowerCase();
    renderTree();
  });
  el("select-all").addEventListener("click", () => {
    state.videos.forEach((video) => state.selection.add(video.id));
    renderVideos();
    renderTree();
    syncButtons();
  });
  el("select-none").addEventListener("click", () => {
    state.selection.clear();
    renderVideos();
    renderTree();
    syncButtons();
  });
  el("select-invert").addEventListener("click", () => {
    const next = new Set();
    state.videos.forEach((video) => {
      if (!state.selection.has(video.id)) next.add(video.id);
    });
    state.selection = next;
    renderVideos();
    renderTree();
    syncButtons();
  });
  el("expand-all").addEventListener("click", () => {
    state.expanded = new Set(allFolderIds());
    renderTree();
  });
  el("collapse-all").addEventListener("click", () => {
    state.expanded = new Set();
    renderTree();
  });

  window.addEventListener("error", (event) => fail(event.message));

  if (el("root").value.trim()) await loadCatalog();
  await checkEnvironment();

  // The first-run path, in the order the user would do it: no session means read one out of the
  // player, and then the catalog is worth fetching. Both are one click, so the window does them
  // rather than describing them.
  if (!state.environment?.session && state.environment?.playerRunning && el("root").value.trim()) {
    await sniffSession({ quiet: true });
  } else {
    await invoke("log_note", {
      message:
        `auto-sniff skipped: player=${state.environment?.playerRunning} ` +
        `session=${Boolean(state.environment?.session)} root=${JSON.stringify(el("root").value)}`,
    }).catch(() => {});
  }
  if (!state.catalog && state.environment?.session && el("account").value && el("root").value.trim()) {
    await refreshCatalog();
  } else if (!state.catalog) {
    await invoke("log_note", {
      message:
        `auto-refresh skipped: session=${Boolean(state.environment?.session)} ` +
        `account=${JSON.stringify(el("account").value)} root=${JSON.stringify(el("root").value)}`,
    }).catch(() => {});
  }
  // One line saying which first-run steps were taken, so a window that "did nothing" can be read
  // back from the log instead of guessed at.
  await invoke("log_note", {
    message:
      `boot: player=${state.environment?.playerRunning} ` +
      `session=${Boolean(state.environment?.session)} ` +
      `catalog=${Boolean(state.catalog)} ` +
      `videos=${state.videos.length}`,
  }).catch(() => {});
  if (state.queue.items.length) renderQueue();
  updateSelectionSummary();
  syncButtons();
}

// `boot` lives behind DOMContentLoaded because the view is split across three files that share
// top-level state: by the time this fires, `catalog.js` and `queue.js` have both been evaluated, so
// `buildTree`, `renderQueue` and the rest are defined.
window.addEventListener("DOMContentLoaded", () => {
  boot().catch((error) => fail(`界面初始化失败：${error}`));
});
