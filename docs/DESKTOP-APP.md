# The desktop window

`evmedia-gui` is the batch surface: a course tree, a selection, and a queue. It is not a second
implementation of anything — every row it runs is one `evmedia export-evs` process, and every
number on screen came out of that process's own JSON event stream.

```powershell
cargo build --release            # target/release/evmedia.exe   — the product
cargo build --release -p evmedia-gui   # target/release/evmedia-gui.exe — the window
```

Put both binaries in the same directory (they already are, in `target/release`) and run
`evmedia-gui.exe`. Nothing else is needed: no Node, no bundler, no installer.

## What it does that the CLI cannot

The CLI exports one lesson per invocation. That is right for a script and wrong for a person with
sixty courses and nineteen hundred videos, so the window adds three things and no more:

| | |
|---|---|
| **A directory** | `evmedia catalog` walked once, then browsed as a tree with per-folder counts and a filter |
| **A batch** | N lessons exported at once — N *processes*, not N threads inside one |
| **A queue that remembers** | Rows keep their state, outputs are recognised, and a rerun resumes instead of re-downloading |

Concurrency has two levels and both are yours to set:

- **同时导出 (workers)** — how many lessons run at once. Each one is an independent
  `evmedia export-evs`, so a lesson that fails takes nothing else down with it.
- **每视频线程 (`--jobs`)** — how many of one lesson's segments download at once, inside the CLI.

Measured on a 12-lesson fixture, three workers finish in 0.71 s where one takes 1.73 s.

## The session, in one click

A segment key is `MD5_hex(tk + filename + "20220507")` and all three inputs come from the API, so
an export needs a bearer token and the catalog's dynamic keys — a short-lived credential that
exists inside the running player.

The window does not ask where that credential is. **自动获取** finds the player by its window,
runs `tools/parser-tools/probe_catalog_constants.py` against it, writes `<export root>/session.json`
and reads the file back through the same validator a hand-picked one would go through. The probe
is not reimplemented: it holds the byte offsets, checked against a known `EVPlayer2.exe`, and
duplicating that would duplicate the part most likely to be wrong.

Two things it needs, both reported in plain language when missing:

- a Python with `frida` — `pip install frida`; `EVMEDIA_PYTHON` overrides which interpreter is used;
- a logged-in EVPlayer2, with a window. `EVMEDIA_PROBE` overrides where the probe script is found.

Attaching is not always instantaneous, so a `PermissionDeniedError` from Frida is retried before it
becomes a message. That failure means "busy for a moment", not "you are not logged in".

## Files it writes, and where

Everything lands under the export root you choose:

```
<root>/
  session.json          the credential (empty path in the settings means this file)
  catalog.json          the tree, written by `evmedia catalog`
  index.json            the CLI's recursive index
  raw/                  the CLI's raw API replies
  work/<course>-<file>/ one lesson's scratch space: enc/, lesson.ts, stderr.log
  out/<目录>/<章节>/<标题>.mp4
```

**The output mirrors the catalog.** A lesson keeps the place it has in the course: every folder
becomes a directory and the lesson's own title becomes the file, so
`AI 大全栈/Python/01. 课程导言/01. 课程导言.mp4` is where that lesson belongs and where it is found.
This is not cosmetic — every chapter restarts its numbering at `01.`, so a flat directory per course
collects a dozen different `01. xxx.mp4` from a dozen chapters.

Two details of that mapping:

- **Consecutive repeats are folded.** The catalog nests a course under a folder of its own name, so
  the raw chain is `前端课程/前端课程/求职之道极速版/求职之道极速版/01.必看导言`. Only *consecutive*
  repeats are collapsed; a name that legitimately reappears deeper is kept.
- **The leaf title is the filename**, with any container extension it already carries replaced:
  the API returns titles ending in `.mp4`, and appending a second one produced
  `01. 课程导言.mp4.mp4`.

A folder name from the course API cannot escape the export root: `\`, `/`, `:` and the rest become
`_`, a segment of nothing but dots becomes `__` (it would otherwise be a parent reference), and
Windows' reserved device names get a trailing underscore.

`--work` stays keyed by `<course>-<file>` rather than by title: it is scratch space, and the ids are
the stable key for it. A single lesson's leftovers are what a failed export is diagnosed from, so
they are never shared between lessons.

To export one chapter, click its folder checkbox in the tree (or double-click the folder's name);
the batch then carries that chapter's directory with it.

## Behaviour worth knowing

- **An existing output is skipped, not overwritten.** Re-running a batch when three videos failed
  re-exports those three. 覆盖已有文件 turns the check off explicitly.
- **`partial` is a failure.** The CLI exits 0 for an incomplete lesson and writes
  `lesson.partial.ts`; the row says 失败, because a file that looks finished and is not is worse
  than no file.
- **A stop is cooperative, and there is a second button.** 停止 writes the CLI's stop file: it
  finishes the segment in flight, merges what it has, and exits — so a stopped lesson still resumes.
  That can take as long as the CLI's own boundaries allow, so once a stop is requested the row says
  正在停止… and 强制结束 becomes available: it kills the processes and the ffmpeg they spawned, and
  leaves the downloaded segments in place for a rerun.
- **An expired session halts the batch.** The window recognises the server's own wording
  (其他设备上登录, `unauthorized`) and stops the run instead of failing two hundred times for one
  reason.
- **Every lesson keeps its full transcript** at `work/<course>-<file>/stderr.log`. The panel in the
  window shows the last 400 lines because it is re-sent on every update; the file is the record, and
  the row has a 完整日志 button that opens it.
- **Nothing is guessed from an exit code.** A run that ends without reporting a status is a failure,
  and the log is drained before the row is settled, because the process exits before its last line
  has necessarily been read.

### The verdict the CLI has to give

The contract says `finished` is the last line of every run, and the window treats a run that ends
without one as a failure — deliberately, because `partial` leaves a file that looks finished. For a
while only `grab` emitted one, so every *successful* `export-evs` ended with a `stage` line and was
reported as 失败: the batch looked completely broken while the files on disk were fine. `main` now
supplies the missing verdict for any command that does not report its own.

A lesson title usually ends in `.mp4` (that is the filename the API returns), so the output name is
built from the title with its container extension dropped: `01. 课程导言.mp4`, not
`01. 课程导言.mp4.mp4`.

### Checking that a file is actually watchable

```powershell
powershell -File tools/verify-media.ps1 -Path 'D:\ev-export\out\432698\01. 课程导言.mp4'
```

It reports the streams, the audio level, the decode errors for each stream, and how far the audio
packets reach. A listed audio stream is not the same as audible sound, and this project has been
wrong about "the export worked" twice by trusting a structural check.

## Verifying it

```powershell
cargo test -p evmedia-gui          # plan, catalog, queue, protocol, config, session, sniff
cargo run -p evmedia-gui --example make_fixture -- verify_gui 12 3
cargo run -p evmedia-gui --example batch_run   -- verify_gui target/release/evmedia-stub.exe 3
```

`make_fixture` writes a catalog in the shape `evmedia catalog` produces, so the batch path can be
exercised without an account, a network or a video; `batch_run` drives the real queue against
`evmedia-stub`, which speaks the same argv and event protocol as the CLI and is not part of the
product. `tools/verify-gui.ps1` launches the window against a fixture and reports what the app
logged and what landed on disk.

The window itself is checked by reading `%APPDATA%\evmedia-gui\app.log`, which records the
environment it found, the session it sniffed, the catalog it refreshed and how a boot ended. A
window with no console has nowhere else to say any of it.
