# evmedia

Turns a lesson in the Chinese course-video player **EVPlayer2** into a playable file.

图形界面：`cargo build --release -p evmedia-gui` 后双击 `target/release/evmedia-gui.exe`。
选导出目录 → 点「自动获取」（从正在运行的 EVPlayer2 里直接读会话，不用找文件）→ 刷新目录 →
勾选课程 → 开始导出。批量、并发、断点续传都在窗口里，见
[桌面端说明](docs/DESKTOP-APP.md)。

播放后自动导出完整 MP4/MKV：双击 `tools/自动导出整课.cmd`，或运行
`python -u tools/export_video.py --cache D:\EVPlayer2_download`，然后在播放器打开课程。
工具从完整 VOD 清单独立下载所有分段，不需要播放到结尾。用法与完整性校验见
[整课导出说明](docs/FULL-VIDEO-EXPORT.md)。

Two binaries ship from one workspace:

| | |
|---|---|
| `evmedia` | the CLI — the product |
| `evmedia-gui` | a desktop window whose every interaction is an `evmedia` CLI invocation |

`evmedia-gui` does not link the engine. It shells out to `evmedia.exe` — one child process per
lesson, N at a time — and every number on screen came out of that process's own JSON event stream.
See [`docs/CLI-CONTRACT.md`](docs/CLI-CONTRACT.md) for the boundary and
[`docs/DESKTOP-APP.md`](docs/DESKTOP-APP.md) for the window.

## Build

```bash
cargo build --release
```

That produces `target/release/evmedia.exe` and nothing else — the GUI pulls in a windowing stack
and is deliberately kept out of the default build, so a problem there can never block the CLI.

```bash
cargo build --release -p evmedia-gui
```

Produces `target/release/evmedia-gui.exe`. Put both in the same folder (or run from
`target/release`) and the GUI finds the CLI by itself. No Node, no bundler: the window's frontend
is plain static HTML/CSS/JS and `cargo` alone builds it.

## Use

```bash
# capture a lesson: harvest keys from the running player, download, decrypt, merge
evmedia grab --pid <EVPlayer2 pid> --output lesson_out --mp4

# the same lesson without the player: ask the API to sign the segments, then derive and decode
evmedia fetch --from-body captured.params --token <bearer> --output list.json
evmedia derive --playlist list.json --input <segments> --output manifest.json
evmedia decode-ev <segments> manifest.json lesson.ts

# the portable commands
evmedia tree examples/catalog.json
evmedia download manifest.json out --parallel 8
evmedia adapters
```

Then either double-click `evmedia-gui.exe` and pick `grab`, or use the command line directly.
The window and the terminal run the same code: the window builds its argv from the CLI's own
argument definitions and re-parses it through the CLI's parser before spawning, so they cannot
drift apart.

### The window's batch model

One lesson per `evmedia export-evs` process, N processes at a time, each with its own `--work`
directory under the export root. A lesson whose output already exists is skipped rather than
re-exported, so re-running a batch finishes only what is missing. A stop is the CLI's stop file,
not a kill, so a stopped lesson merges what it has and resumes next time.

## Layout

```
crates/
  evmedia-contract/   argv + event schema + exit codes; the only thing the GUI shares
  evmedia-core/       catalog, download, decode, crypto, harvest loop — no Windows API at all
  evmedia-win/        process memory scanning, key derivation, playhead control
  evmedia/            the CLI
  evmedia-gui/        the desktop window (Tauri + static frontend, no bundler)
    src/              catalog, plan, job, queue, protocol, server, sniff, session, refresh, log
    dist/             the view: plain HTML/CSS/JS, three files sharing top-level state
    examples/         make_fixture and batch_run: the batch path without a window
tools/                Python reverse-engineering helpers, the session probe, the GUI driver
docs/                 architecture, the CLI contract, and the desktop window
```

No source file exceeds 500 lines, and `cargo test` fails if that changes.

## Tests

```bash
cargo test --workspace
```

The suite includes the harvest loop driven by a fixture — no player, no Windows, no network —
plus checks that the portable core never reaches for a Windows API, that the GUI cannot link the
core, and that the GUI's argv construction stays the exact inverse of clap's parsing.

The window adds its own: the queue driven end to end against `evmedia-stub` (a test double for the
wire that speaks the same argv and events), and that only the CLI's own `complete` status counts as
a success.

## Background

`HANDOFF.md` is the project's working notes, including the device-identity launcher that lets
EVPlayer2 run outside Windows Sandbox, and the documented decryption scheme that this build
turned out **not** to use.

`docs/KEY-DERIVATION.md` is the key derivation as read off `PlayerLibRender56_vs.dll`: the whole
chain from the context's `tk` and filename to the AES schedule, and the one input that is not on
the wire — which is why a lesson still costs one playback pass.
