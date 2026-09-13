# Architecture

`evmedia` captures a lesson from a running EVPlayer2, decrypts its segments, and produces a
playable file. It ships as two binaries: a CLI, and a desktop window that drives that CLI.

## Crates

```
evmedia-contract  ←  evmedia-core  ←  evmedia-win  ←  evmedia (CLI)
      ↑
 evmedia-gui
```

| Crate | What it is | What it may depend on |
|---|---|---|
| `evmedia-contract` | The one shared surface: the argv shape, the event schema, the exit codes, the reporter. No logic, no I/O, no platform code. | clap, serde |
| `evmedia-core` | The portable product: catalog, download, decode, crypto, and the harvest loop. **Makes no Windows API call.** | `evmedia-contract`, crypto/http crates |
| `evmedia-win` | Everything that needs Windows: reading the player's memory, deriving keys from playback contexts, driving the playhead. | `evmedia-core`, `windows-sys` |
| `evmedia` | The CLI. One function per subcommand; `grab` and `capture-ev` are the only platform-gated ones. | all of the above |
| `evmedia-gui` | The desktop window. Deliberately **not** linked against the core — see below. | `evmedia-contract`, tauri |

Each of these rows is enforced rather than intended. `crates/evmedia/tests/source_budget.rs`
asserts that `evmedia-core/src` never mentions `windows_sys`, and that `evmedia-gui`'s manifest
lists neither `evmedia-core` nor `evmedia-win`.

## The two seams

**`harvest::Harvester`** is what keeps Windows out of the harvest loop. The loop knows about
segments, keys, gaps and the playhead; `evmedia-win::WinSource` is the only real implementation,
and a fixture in `crates/evmedia-core/tests/grab_loop.rs` is the other. That is why the loop's
resume, retry, completeness and merge behaviour can be tested with no player, no Windows and no
network. `seek_to` defaults to `Ok(false)`, which is the honest answer for a source with no
playhead.

**`evmedia-contract`** is what keeps the GUI and the CLI from disagreeing. The argv is defined
once, in `args.rs`; the GUI builds the same structs and calls `ToArgv`, and re-parses every argv
through `Cli::try_parse_from` before spawning, so it cannot run something the CLI would reject.
The GUI's forms are generated from `describe()`, which walks the clap definition at runtime —
a new flag shows up in the window without a second edit. The event stream and exit codes are
frozen in `docs/CLI-CONTRACT.md`.

## Why the GUI cannot link the core

The requirement is that the window's interactions *are* CLI runs. The obvious way to satisfy
that is by discipline, which decays. Instead, `evmedia-gui`'s manifest omits `evmedia-core` and
`evmedia-win`, so an engineer reaching for `core::harvest::grab::run` from the GUI cannot import
it, and the test suite fails if anyone adds the dependency back.

The window therefore shells out to `evmedia.exe`. Its Rust side spawns the process, parses the
JSON event lines and forwards them to the web view; the view is plain static HTML/CSS/JS with no
bundler, so `cargo build` alone produces the binary.

## Platform gating

Two independent mechanisms, so a non-Windows `cargo check --workspace` succeeds:

- `evmedia-win/src/lib.rs` starts with `#![cfg(windows)]`, so the whole crate compiles to an
  empty shell off Windows and no module inside needs its own `#[cfg]`;
- `windows-sys` appears only under `[target.'cfg(windows)'.dependencies]`, so it is not even
  resolved on other platforms.

`default-members = ["crates/evmedia"]` means a plain `cargo build --release` produces only the
CLI. The GUI pulls in a windowing stack, and a broken WebView or a newer MSRV requirement in it
must never be able to block the command line tool. Build the GUI explicitly:

```
cargo build --release -p evmedia-gui
```

## How a lesson is captured

1. The player holds two things in readable memory while a lesson is open: playback-context
   objects, and the fully signed segment URLs it built for its own requests.
2. A context carries the segment index (`+8`), the filename (`+0x18`), and a 32-byte key-schedule
   slot (`+0x120`). `scan::segment_indexes` and `scan::segment_urls` find them by their vtable
   range, and the loop joins the two on filename.
3. Keys come from two sources, and both are needed. A live context carries the key in its
   key-schedule slot (`+0x120`), which `scan::active_keys` reconstructs — exact, cheap, and it
   brings the index and the filename with it. A key outlives its context, though, and once the
   context is gone it is only a loose 32-hex string on the heap: `scan::hex_candidates` collects
   those and `keyscan` tests each against the segments' own bytes. Neither covers the other's
   ground, so the loop consults both.
4. Each segment is fetched, cached under `enc/`, decrypted into `dec/`, and the whole set is
   merged in index order.
5. The order of those steps is load-bearing in one specific way: **the download must never be
   gated on already holding a key.** Downloading needs none — only decrypting does — so gating it
   is circular, and a segment whose key has not arrived yet would never be fetched at all.

### The key-schedule slot

`+0x120` holds the segment key once the player has decrypted that segment: the first 16 bytes
verbatim, the second 16 MixColumns'd, which `crypto::schedule_to_key` inverts. It reads
`0xBAADF00D` until then, which is how "not decrypted yet" is detected.

This was believed disproven for a while, and the code that read it was deleted on that belief. The
belief came from a test that was itself broken — a probe length that was not AES-block aligned, so
AES rejected it and every key was read as a wrong key. The conclusion outlived the bug it came
from. Measured directly against a live player (`tools/parser-tools/probe_schedule_key.py`): 333
filled slots yielded 333 keys and opened 333 segments, with no failures.

Its limit is coverage, not correctness — it sees only segments the player still holds a context
for. `scan::segment_indexes` deliberately walks *every* context, decrypted or not, because that is
what gap detection needs: a segment the playhead skipped has a context and no key.

### Keys exist only during playback

This is the constraint the whole design works around. A segment's key is computed when the
player decrypts it for playback, and nowhere else — the download path does not compute it. A
segment the playhead never reached therefore has no key anywhere, and waiting will not produce
one. That is what the sweep is for: `evmedia-win::playhead` posts `WM_KEYDOWN`/`WM_KEYUP` to the
player's own window to walk the playhead back over a gap, which needs no focus, no injection and
no UI coordinates.

### The salt formula does not apply to this build

`HANDOFF.md` records an earlier, disproven line of attack. The documented derivation
`key = MD5(tk + filename + salt)` is **not** what this build does: a direct search of process
memory for the concatenation `tk + filename` returned nothing, and neither did ~1.1 million
candidate combinations. An earlier collector (`src/evplayer_windows.rs`, since replaced by
`evmedia-win/src/capture.rs`) rested entirely on that formula and could therefore only ever fail.
`capture-ev` now recovers keys the same way everything else does: by testing heap candidates
against the segment's own bytes.

## Data contracts

- **`Catalog`** — course folders and video leaves, for browsing and selection.
- **`DownloadManifest v1`** — authorised segment URLs, headers, ordering, optional SHA-256.
  Downloads are written atomically and may run concurrently.
- **`EvManifest`** — an ordered set of encrypted segments plus the key material for each.
- **`CaptureManifest`** — what `capture-ev` writes; the same three top-level fields the previous
  collector emitted, so existing `decode-ev` invocations keep working.

Other platforms (Android, macOS, iOS) need their own adapters. Stock iOS does not let one app
inspect another's memory, so its adapter would have to receive data through an authorised
in-app export instead.
