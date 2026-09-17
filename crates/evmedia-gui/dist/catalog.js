// The course tree (left) and the selection list (middle).
//
// Both are rendered from the `catalog.json` the CLI wrote, so the window never invents a course
// structure: a folder is a folder because `evmedia catalog` said so.

// Every video id at or below a node, matching the Rust side's `Node::video_ids`.
function videoIdsOf(node) {
  if (node.video) return [node.video.id];
  return (node.children ?? []).flatMap(videoIdsOf);
}

function allFolderIds() {
  const out = [];
  const walk = (node) => {
    if (node.video) return;
    out.push(node.id);
    (node.children ?? []).forEach(walk);
  };
  (state.catalog?.roots ?? []).forEach(walk);
  return out;
}

// Walk the tree keeping the path down, so a row can say where it is in the course.
//
// Consecutive repeats are folded away for the same reason the Rust side folds them: the catalog
// nests a course under a folder of its own name, so the raw chain reads
// `前端课程 / 前端课程 / 求职之道极速版 / 求职之道极速版`. That is a correct tree and an absurd path.
function fillPaths(node, trail, out) {
  if (node.video) {
    out.push({
      id: node.video.id,
      title: node.title,
      duration: node.video.durationSeconds ?? null,
      path: [...trail],
    });
    return;
  }
  const name = (node.title ?? "").trim();
  const next = name && trail[trail.length - 1] !== name ? [...trail, name] : trail;
  (node.children ?? []).forEach((child) => fillPaths(child, next, out));
}

function selectedCountOf(node) {
  return videoIdsOf(node).filter((id) => state.selection.has(id)).length;
}

function matchesFilter(node) {
  if (!state.filter) return true;
  if ((node.title ?? "").toLowerCase().includes(state.filter)) return true;
  if (node.video) return false;
  return (node.children ?? []).some(matchesFilter);
}

function buildTree() {
  const roots = state.catalog?.roots ?? [];
  state.videos = [];
  roots.forEach((root) => fillPaths(root, [], state.videos));
  // A selection that is no longer in the catalog (the root changed, the course list changed) must
  // not keep counting towards the batch.
  const known = new Set(state.videos.map((video) => video.id));
  state.selection = new Set([...state.selection].filter((id) => known.has(id)));
  renderTree();
  renderVideos();
  syncButtons();
}

function renderTree() {
  const host = el("tree");
  host.replaceChildren();
  const roots = state.catalog?.roots ?? [];

  if (!roots.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.catalog
      ? "这个账号下没有可导出的课程"
      : "还没有课程目录。确认会话和导出目录后点“刷新目录”。";
    host.append(empty);
    updateTreeSummary();
    return;
  }

  for (const root of roots) {
    const node = renderNode(root, 0);
    if (node) host.append(node);
  }
  updateTreeSummary();
}

function renderNode(node, depth) {
  if (!matchesFilter(node)) return null;

  if (node.video) {
    const row = document.createElement("label");
    row.className = "tree-video" + (state.selection.has(node.video.id) ? " on" : "");
    row.style.paddingLeft = `${8 + depth * 14}px`;

    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.selection.has(node.video.id);
    box.addEventListener("change", () => {
      toggleSelection(node.video.id, box.checked);
      renderTree();
      renderVideos();
    });

    const text = document.createElement("span");
    text.className = "tree-title";
    text.textContent = node.title;
    row.append(box, text);
    return row;
  }

  const wrap = document.createElement("div");
  wrap.className = "tree-folder";

  const head = document.createElement("div");
  head.className = "tree-head";
  head.style.paddingLeft = `${4 + depth * 14}px`;

  const ids = videoIdsOf(node);
  const chosen = selectedCountOf(node);

  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = ids.length > 0 && chosen === ids.length;
  box.indeterminate = chosen > 0 && chosen < ids.length;
  box.disabled = ids.length === 0;
  box.title = "选择这个目录下的全部视频";
  box.addEventListener("click", (event) => event.stopPropagation());
  box.addEventListener("change", () => setSelectionForFolder(node, box.checked));

  const open = state.expanded.has(node.id) || Boolean(state.filter);
  const twisty = document.createElement("button");
  twisty.type = "button";
  twisty.className = "twisty";
  twisty.textContent = open ? "▾" : "▸";
  twisty.addEventListener("click", (event) => {
    event.preventDefault();
    if (state.expanded.has(node.id)) state.expanded.delete(node.id);
    else state.expanded.add(node.id);
    renderTree();
  });

  const title = document.createElement("span");
  title.className = "tree-title";
  title.textContent = node.title;

  const count = document.createElement("span");
  count.className = "tree-count";
  count.textContent = ids.length ? `${chosen}/${ids.length}` : "";

  head.append(twisty, box, title, count);
  // Double-click is the shortcut for "export this whole chapter": it selects or clears every video
  // below the folder, which is the same thing the checkbox does.
  head.title = "双击：全选 / 取消这个目录下的全部视频";
  head.addEventListener("dblclick", () => {
    setSelectionForFolder(node, chosen < ids.length);
  });
  wrap.append(head);

  if (open) {
    const body = document.createElement("div");
    body.className = "tree-body";
    let any = false;
    for (const child of node.children ?? []) {
      const rendered = renderNode(child, depth + 1);
      if (rendered) {
        body.append(rendered);
        any = true;
      }
    }
    if (any) wrap.append(body);
  }
  return wrap;
}

function updateTreeSummary() {
  el("tree-summary").textContent = state.catalog
    ? `${state.catalog.title || "课程目录"} · ${allFolderIds().length} 个目录 · ${state.videos.length} 个视频`
    : "还没有目录";
  const foot = el("tree-summary").parentElement;
  if (foot && !el("tree-tip")) {
    const tip = document.createElement("span");
    tip.id = "tree-tip";
    tip.className = "muted small";
    tip.textContent = "勾目录=整章，双击目录名=全选/取消";
    foot.insertBefore(tip, el("expand-all"));
  }
}

function formatDuration(seconds) {
  if (!seconds || !Number.isFinite(seconds)) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${minutes}:${String(secs).padStart(2, "0")}`;
}

// Where the file will land, relative to the export root's `out/`: the catalog's own folders, then
// the lesson's title. Shown so the user can see the structure before a 200-video batch writes it;
// the authoritative path is the one the Rust side plans, and this mirrors it.
function outputPathOf(video) {
  const safe = (name) => (name || "").replace(/[\\/:*?"<>|]/g, "_").trim();
  const folders = video.path.map(safe).filter(Boolean);
  const extension = state.settings.extension || "mp4";
  const stem = safe((video.title || "video").replace(/\.[A-Za-z0-9]{2,4}$/, ""));
  return [...folders, `${stem}.${extension}`].join(" / ");
}

function renderVideos() {
  const host = el("videos");
  host.replaceChildren();
  if (!state.videos.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "刷新目录后，这里会列出每一节课。";
    host.append(empty);
    updateSelectionSummary();
    return;
  }

  const table = document.createElement("div");
  table.className = "video-table";

  const head = document.createElement("div");
  head.className = "video-row head";
  const columns = [
    ["", "c-check"],
    ["课程 / 章节", "c-path"],
    ["标题", "c-title"],
    ["时长", "c-time"],
    ["输出位置（相对 out/）", "c-out"],
  ];
  for (const [label, cls] of columns) {
    const cell = document.createElement("span");
    cell.className = cls;
    cell.textContent = label;
    head.append(cell);
  }
  table.append(head);

  for (const video of state.videos) {
    const row = document.createElement("label");
    row.className = "video-row" + (state.selection.has(video.id) ? " on" : "");

    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "c-check";
    check.checked = state.selection.has(video.id);
    check.addEventListener("change", () => {
      toggleSelection(video.id, check.checked);
      row.classList.toggle("on", check.checked);
      renderTree();
      renderVideos();
    });

    const path = document.createElement("span");
    path.className = "c-path";
    path.textContent = video.path.join(" / ");
    path.title = path.textContent;

    const title = document.createElement("span");
    title.className = "c-title";
    title.textContent = video.title;
    title.title = `${video.id}\n${video.title}`;

    const time = document.createElement("span");
    time.className = "c-time";
    time.textContent = formatDuration(video.duration);

    const out = document.createElement("span");
    out.className = "c-out";
    out.textContent = outputPathOf(video);
    out.title = out.textContent;

    row.append(check, path, title, time, out);
    table.append(row);
  }
  host.append(table);
  updateSelectionSummary();
}

function updateSelectionSummary() {
  const chosen = state.videos.filter((video) => state.selection.has(video.id));
  const seconds = chosen.reduce((sum, video) => sum + (video.duration || 0), 0);
  el("selection-summary").textContent = chosen.length
    ? `选中 ${chosen.length} / ${state.videos.length} 个视频 · 共 ${formatDuration(seconds)}`
    : `没有选中视频（共 ${state.videos.length} 个）`;
}
