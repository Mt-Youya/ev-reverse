// The window is a thin shell over `evmedia.exe`.
//
// It never decides what a command means: the list of commands, their arguments, their help text
// and their defaults all come from the CLI's own clap definition via `command_spec`. What this
// file does is turn field values into an argv, run that argv as a child process, and render the
// events and text the process emits.

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const el = (id) => document.getElementById(id);

const state = {
  spec: [],
  current: null,
  fields: new Map(),
  running: false,
  cli: null,
  workdir: null,
};

// ---------------------------------------------------------------- CLI lookup

async function refreshCli(override) {
  const status = await invoke("check_cli", { overridePath: override ?? null });
  state.cli = status;
  const chip = el("cli-status");
  chip.className = "chip " + (status.ok ? "chip-ok" : "chip-bad");
  chip.textContent = status.ok
    ? `v${String(status.version).replace(/^evmedia\s*/, "")} · ${status.source} · ${status.path}`
    : status.detail;
  chip.title = status.path || status.detail;
  updateButtons();
}

// ------------------------------------------------------------------- forms

function renderCommands() {
  const nav = el("commands");
  nav.replaceChildren();
  for (const command of state.spec) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = command.name;
    button.title = command.about;
    button.addEventListener("click", () => select(command));
    nav.append(button);
  }
}

function select(command) {
  if (state.running) return;
  state.current = command;
  state.fields.clear();

  for (const button of el("commands").children) {
    button.classList.toggle("active", button.textContent === command.name);
  }
  el("command-title").textContent = command.name;
  el("command-about").textContent = command.about;

  const form = el("form");
  form.replaceChildren();
  for (const arg of command.args) {
    form.append(buildField(arg));
  }
  updatePreview();
  updateButtons();
}

function buildField(arg) {
  const wrap = document.createElement("div");
  const label = arg.long ? `--${arg.long}` : arg.id;

  if (arg.boolean) {
    wrap.className = "field check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = `f-${arg.id}`;
    input.addEventListener("change", updatePreview);
    const text = document.createElement("label");
    text.htmlFor = input.id;
    text.textContent = arg.help || label;
    wrap.append(input, text);
    state.fields.set(arg.id, input);
    return wrap;
  }

  wrap.className = "field";
  const text = document.createElement("label");
  text.textContent = label + (arg.required && !arg.positional ? "（必填）" : "");
  if (arg.positional) text.textContent = `${arg.id}（位置参数${arg.required ? "，必填" : ""}）`;
  wrap.append(text);

  const row = document.createElement("div");
  row.className = "row";
  const input = document.createElement("input");
  input.type = arg.numeric ? "number" : "text";
  input.id = `f-${arg.id}`;
  input.placeholder = arg.default ?? (arg.numeric ? "数字" : "");
  if (arg.default !== null && arg.default !== undefined && !arg.numeric) {
    input.value = arg.default;
  }
  input.addEventListener("input", updatePreview);
  row.append(input);

  if (arg.path) {
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "small";
    pick.textContent = "选择…";
    pick.addEventListener("click", async () => {
      const chosen = await invoke("pick_directory", { current: input.value || null });
      if (chosen) {
        input.value = chosen;
        updatePreview();
      }
    });
    row.append(pick);
  }

  wrap.append(row);
  if (arg.help) {
    const help = document.createElement("span");
    help.className = "help";
    help.textContent = arg.help;
    wrap.append(help);
  }
  state.fields.set(arg.id, input);
  return wrap;
}

// ----------------------------------------------------------------- argv

function valueOf(id) {
  const field = state.fields.get(id);
  if (!field) return null;
  return field.type === "checkbox" ? field.checked : field.value.trim();
}

function buildArgv() {
  const command = state.current;
  if (!command) return [];
  const argv = [command.name];
  const positionals = [];
  for (const arg of command.args) {
    const value = valueOf(arg.id);
    if (arg.boolean) {
      if (value) argv.push(`--${arg.long}`);
      continue;
    }
    if (arg.positional) {
      if (value !== "") positionals.push(value);
      continue;
    }
    // An empty optional argument is left out so the CLI applies its own default.
    if (value === "") continue;
    argv.push(`--${arg.long}`, value);
  }
  return argv.concat(positionals);
}

function workdirOf() {
  const value = valueOf("output");
  if (value) return value;
  return state.workdir || ".";
}

function quote(token) {
  return /[\s"]/.test(token) ? `"${token.replace(/"/g, '\\"')}"` : token;
}

function updatePreview() {
  const argv = buildArgv();
  const stop = `${workdirOf()}/.evmedia-stop`;
  const full = argv.concat(["--json-events", "--stop-file", stop]);
  el("argv").textContent = quote(state.cli?.path || "evmedia.exe") + " " + full.map(quote).join(" ");
  updateButtons();
}

// ------------------------------------------------------------------ running

function updateButtons() {
  const ready = Boolean(state.cli?.ok);
  el("run").disabled = !ready || state.running || !state.current;
  el("cancel").disabled = !state.running;
  el("force").disabled = !state.running;
}

function setRunning(running) {
  state.running = running;
  el("run").textContent = running ? "运行中…" : "运行";
  updateButtons();
}

function appendLog(line) {
  const log = el("log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
  log.textContent += (log.textContent ? "\n" : "") + line;
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function banner(text, kind) {
  const node = el("banner");
  node.textContent = text;
  node.className = `banner ${kind}`;
  node.classList.remove("hidden");
}

function setStat(id, value) {
  el(id).textContent = value;
}

const STAGE_LABEL = { scan: "扫描/抓取", download: "下载", merge: "合并", remux: "转 MP4" };

function handleEvent(event) {
  switch (event.event) {
    case "started":
      appendLog(`[协议 v${event.protocol} · CLI ${event.app_version}] ${event.command.join(" ")}`);
      break;
    case "stage":
      setStat("stat-stage", `${STAGE_LABEL[event.name] || event.name}${event.state === "begin" ? "…" : " ✓"}`);
      break;
    case "progress":
      setStat("stat-keys", event.keys);
      setStat("stat-segments", event.segments);
      setStat("stat-done", event.done);
      setStat("stat-failed", event.failed);
      setStat("stat-elapsed", `${event.elapsed_secs}s`);
      {
        const total = Math.max(event.segments, event.done);
        el("bar-fill").style.width = total ? `${Math.round((event.done / total) * 100)}%` : "0%";
      }
      break;
    case "segment":
      if (event.state === "failed") {
        appendLog(`[分片 ${event.index} 失败 第${event.attempt}次] ${event.error || ""}`);
      }
      break;
    case "artifact":
      appendLog(`[产物 ${event.kind}] ${event.path} (${event.bytes} 字节)`);
      banner(`已生成：${event.path}`, "ok");
      break;
    case "finished": {
      const kind = event.status === "complete" ? "ok" : event.status === "failed" ? "bad" : "warn";
      const text = {
        complete: "完成：整节课都拿到了",
        partial: "不完整：只合并了已解密的片段（lesson.partial.ts）",
        nothing: "没有抓到任何片段",
        cancelled: "已取消（已下载的片段保留，重跑会续传）",
        failed: "失败",
      }[event.status] || event.status;
      banner(`${text}${event.message ? " — " + event.message : ""}`, kind);
      break;
    }
    default:
      appendLog(JSON.stringify(event));
  }
}

async function onPayload(message) {
  const payload = message.payload;
  if (payload.kind === "event") {
    handleEvent(payload.event);
  } else if (payload.kind === "log") {
    appendLog(payload.line);
  } else if (payload.kind === "exit") {
    setRunning(false);
    appendLog(`[进程结束 exit=${payload.code}]`);
  }
}

// ------------------------------------------------------------------- wiring

async function boot() {
  await refreshCli();
  state.spec = await invoke("command_spec");
  renderCommands();

  const config = await invoke("get_config");
  state.workdir = config.lastOutput || null;

  if (config.cliPath) await refreshCli(config.cliPath);

  await listen("job", onPayload);
  setRunning(await invoke("job_running"));

  const preferred = state.spec.find((command) => command.name === "grab") || state.spec[0];
  if (preferred) select(preferred);

  el("run").addEventListener("click", async () => {
    el("banner").classList.add("hidden");
    const argv = buildArgv();
    const workdir = workdirOf();
    try {
      await invoke("start_job", { cli: state.cli.path, argv, workdir });
      state.workdir = workdir;
      await invoke("set_config", {
        settings: { cliPath: state.cli.path, lastOutput: workdir, lastPid: Number(valueOf("pid")) || null },
      });
      setRunning(true);
      el("bar-fill").style.width = "0%";
      for (const id of ["stat-keys", "stat-segments", "stat-done", "stat-failed"]) setStat(id, "0");
      setStat("stat-elapsed", "0s");
      setStat("stat-stage", "启动中…");
      appendLog(`$ ${argv.join(" ")}`);
    } catch (error) {
      banner(String(error), "bad");
    }
  });

  el("cancel").addEventListener("click", async () => {
    try {
      await invoke("cancel_job");
    } catch (error) {
      banner(String(error), "bad");
    }
  });

  el("force").addEventListener("click", async () => {
    try {
      await invoke("force_kill");
    } catch (error) {
      banner(String(error), "bad");
    }
  });

  el("copy").addEventListener("click", async () => {
    await navigator.clipboard.writeText(el("argv").textContent);
    el("copy").textContent = "已复制";
    setTimeout(() => (el("copy").textContent = "复制"), 1200);
  });

  el("clear-log").addEventListener("click", () => {
    el("log").textContent = "";
  });

  window.addEventListener("error", (event) => banner(String(event.message), "bad"));
}

boot().catch((error) => banner(`界面初始化失败：${error}`, "bad"));
