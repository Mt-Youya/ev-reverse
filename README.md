# evmedia

Turns a lesson in the Chinese course-video player **EVPlayer2** into a playable file.

Two binaries ship from one workspace:

| | |
|---|---|
| `evmedia` | the CLI — the product |
| `evmedia-gui` | a desktop window whose every interaction is an `evmedia` CLI invocation |

`evmedia-gui` does not link the engine. It shells out to `evmedia.exe`, streams that process's
JSON events into a progress bar, and shows its stderr in a log pane. See
[`docs/CLI-CONTRACT.md`](docs/CLI-CONTRACT.md).

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

# the portable commands
evmedia tree examples/catalog.json
evmedia download manifest.json out --parallel 8
evmedia decode-ev segments.zip manifest.json lesson.ts
evmedia adapters
```

中文导出流程、课时选择、管理员权限与成品验证见 [CLI-EXPORT.md](docs/CLI-EXPORT.md)；
实机发现的问题、根因与修复见 [CLI-INCIDENTS.md](docs/CLI-INCIDENTS.md)。

Then either double-click `evmedia-gui.exe` and pick `grab`, or use the command line directly.
The window and the terminal run the same code: the GUI's forms are generated from the CLI's own
argument definitions, so they cannot drift apart.

### What `grab` needs from you

The player must have the lesson **open**, and it has to play through it. A segment's decryption
key is computed when the player decrypts that segment for playback and nowhere else, so a part
of the lesson the player never reached has no key to find. `grab` watches the player, harvests
keys as they appear, and — if the playhead has already run past a gap — walks the playhead back
over it on its own. Segments already decrypted are cached, so rerunning resumes instead of
starting over.

## Layout

```
crates/
  evmedia-contract/   argv + event schema + exit codes; the only thing the GUI shares
  evmedia-core/       catalog, download, decode, crypto, harvest loop — no Windows API at all
  evmedia-win/        process memory scanning, key derivation, playhead control
  evmedia/            the CLI
  evmedia-gui/        the desktop window (Tauri + static frontend)
tools/                Python reverse-engineering helpers and the device-identity launcher
docs/                 architecture and the CLI contract
```

No source file exceeds 500 lines, and `cargo test` fails if that changes.

## Tests

```bash
cargo test --workspace
```

The suite includes the harvest loop driven by a fixture — no player, no Windows, no network —
plus checks that the portable core never reaches for a Windows API, that the GUI cannot link the
core, and that the GUI's argv construction stays the exact inverse of clap's parsing.

## Background

`HANDOFF.md` is the project's working notes, including the device-identity launcher that lets
EVPlayer2 run outside Windows Sandbox, and the documented decryption scheme that this build
turned out **not** to use.
