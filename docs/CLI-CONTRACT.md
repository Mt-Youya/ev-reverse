# The evmedia CLI contract

`evmedia-gui` drives `evmedia` as a child process. This document is the whole interface between
them. It is deliberately small: an argv shape, a line-oriented event stream, three exit codes,
and one file used to ask for a stop.

Anything not described here is not part of the contract and the GUI must not depend on it.

## 1. Locating the binary

`evmedia-gui` resolves `evmedia.exe` in this order and shows the winner in its title bar:

1. a path saved from the settings pane (`%APPDATA%/evmedia-gui/config.json`);
2. the `EVMEDIA_CLI` environment variable;
3. next to `evmedia-gui.exe` itself — a workspace build puts every binary in the same
   `target/release`, so a development build needs no configuration;
4. each directory on `PATH`.

If none of them resolves, the GUI says so, with the build command, instead of opening an empty
window. At startup the GUI runs `evmedia --version` as a handshake.

## 2. Command line

The argv is defined once, in `evmedia-contract::args`, and both sides use that one definition.
The GUI builds a `Command` value and calls `ToArgv`; the CLI parses the same structs with clap.
`tests/argv_roundtrip.rs` asserts that `ToArgv` is the exact inverse of that parsing, and
`evmedia-gui` re-parses every argv through `Cli::try_parse_from` before spawning — so the GUI can
never run something the CLI would reject.

The GUI's forms are not hand-written: `describe()` walks the clap definition at runtime and the
window renders one field per argument. A new flag appears in the GUI without a second edit.

### Global flags the GUI adds itself

| Flag | Meaning |
|---|---|
| `--json-events` | stdout becomes newline-delimited JSON; human output moves to stderr |
| `--stop-file <PATH>` | stop cleanly as soon as `PATH` exists |

These are the only two arguments the CLI accepts that do not appear as form fields.

## 3. Output channels

| | `--json-events` off (default) | `--json-events` on |
|---|---|---|
| human text | stdout | **stderr** |
| events | discarded | **stdout**, one JSON object per line |

With the flag off, output is byte-for-byte what it was before the GUI existed, which is why
`evmedia grab … | tee log` still works. With it on, the GUI's log pane shows the CLI's stderr and
its progress bar is fed by the JSON lines — so the pane is a faithful transcript of a terminal
run, and the two channels never interleave.

Events are flushed per line. A line that fails to parse is treated as **plain log text**, never
as an error: that is what lets a newer CLI add event variants without breaking an older GUI.

## 4. Event schema

`serde`-tagged on `event`, snake_case. `PROTOCOL_V1 = 1`; the GUI refuses to drive a CLI whose
`started.protocol` it does not recognise rather than guessing.

```jsonc
{"event":"started","protocol":1,"command":["grab","--pid","23428"],"app_version":"0.1.0"}
{"event":"stage","name":"scan","state":"begin","detail":""}
{"event":"progress","stage":"scan","keys":97,"urls":98,"segments":97,"done":227,"failed":0,"elapsed_secs":120}
{"event":"segment","index":131,"file":"119354-….ts","state":"failed","attempt":1,"error":"download timed out"}
{"event":"log","level":"info","message":"  caught up with the player"}
{"event":"artifact","kind":"ts","path":"rust_out/lesson.ts","bytes":209156016}
{"event":"finished","status":"partial","exit_code":0,"message":"merged 227 segment(s)"}
```

- `stage.name` ∈ `scan | download | merge | remux`; `state` ∈ `begin | end`.
- `segment.state` ∈ `done | failed`.
- `artifact.kind` ∈ `ts | mp4 | manifest`.
- `finished.status` ∈ `complete | partial | nothing | cancelled | failed`.

`progress` is emitted once per poll; `segment` immediately, to keep the UI live. Because events
carry no human text, emitting `progress` every poll does not change the terminal's cadence — the
ten-second summary line is a separate `info` call, exactly as before.

## 5. Exit codes

Frozen at the values the CLI returned before the workspace split:

| Code | Meaning |
|---|---|
| `0` | the command ran — including "the lesson is incomplete, wrote `lesson.partial.ts`" and "harvested nothing" |
| `1` | a runtime error (what `anyhow` produced as `Error: …` on stderr) |
| `2` | the arguments did not parse (clap) |

"No exit code was invented for an incomplete capture" is deliberate: scripts already branch on
`0`, and `finished.status` expresses the same thing with more detail and room to grow. In JSON
mode a runtime error additionally emits `finished{status:"failed"}` before exiting `1`.

## 6. Cancelling

Two levels, in this order:

1. **Graceful.** The GUI creates `<output>/.evmedia-stop`, which it passed as `--stop-file`.
   The CLI checks it at every poll boundary and inside its sleep, so it is noticed within about
   a quarter second — except while an HTTP fetch is already in flight, which is bounded by the
   request timeout. The CLI then merges what it has, emits
   `finished{status:"cancelled"}`, and **exits 0**. Nothing already decrypted is lost.
2. **Forced.** If the user insists, the GUI terminates the job object holding the run, which
   also takes down an ffmpeg child. `Child::kill` alone would orphan it.

Because a graceful stop can wait up to the request timeout, the UI says "waiting for the current
segment" rather than implying the stop is instant. The request timeout is not shortened to make
cancellation snappier: that would change fetch behaviour.

## 7. What the GUI deliberately cannot do

`crates/evmedia-gui/Cargo.toml` does not depend on `evmedia-core` or `evmedia-win`, and
`crates/evmedia/tests/source_budget.rs` fails if that changes. The window's only model of the
product is "a set of CLI invocations", so behaviour cannot drift between the two surfaces
because there is only one implementation of it.
