# Spec: where an export's time actually goes

Status: needs-triage

"Make an export faster" is a request with no measured target behind it, and the obvious reading of it —
the merge loop is sequential while a parallel decoder sits in the same crate — turns out to be worth
about one percent. This spec records the measurement before anyone spends a day on the wrong phase.

## Problem Statement

`export-evs` (`full_export::run`) is the path the window runs, one process per lesson. It was written
as a straight line: seed from the cache, validate every segment twice, download what is missing, build
a manifest, merge sequentially, publish. Four of those phases walk the whole lesson again, so the
suspicion was that the merge and the validation passes dominate a lesson's wall time.

They do not. Measured on the development machine (i5-12400F, 12 threads, NVMe, RTX 4060), a 300 MiB /
346-segment lesson:

| phase | seconds | MiB/s | vs the merge |
| --- | --- | --- | --- |
| sequential merge (what `full_export::run` does) | 0.746 | 402 | 1.00x |
| `decode_ev_parallel`, 1 job | 0.568 | 528 | 0.76x |
| `decode_ev_parallel`, all cores | **0.221** | 1357 | 0.30x |
| one `valid_cipher` pass | 0.376 | 798 | 0.50x |
| `build_manifest_of` (SHA-256 over the lesson) | 0.295 | 1018 | 0.40x |
| bare read (IO floor) | 0.131 | 2289 | 0.18x |
| bare AES-256-ECB, in memory | 0.255 | 1178 | 0.34x |

Reproduce with `cargo run --release -p evmedia-core --example merge_bench -- <cache-dir> [MiB]`. The
fixtures are generated from the real cache's filenames and sizes — a real lesson's keys exist only in
the player's memory — so the work measured (read, SHA-256, XOR, AES-256-ECB, write) is the same work
with different bytes.

## What the whole lesson costs

Same machine, 60-minute 1080p30 lesson at the ~800 kbps a screen recording actually uses, measured
with the ffmpeg the window is pointed at:

| phase | measured | for a 60-minute lesson |
| --- | --- | --- |
| download ~340 MiB | not measured — needs a live session | bandwidth-bound: 27 s at 100 Mbps, 136 s at 20 Mbps |
| two `valid_cipher` passes | 0.38 s each | **0.76 s** |
| `build_manifest_of` | 0.30 s | **0.30 s** |
| sequential merge | 0.75 s | **0.75 s** (0.22 s parallel) |
| ffmpeg remux (`-c copy`) | 0.13 s / 53 MiB | **~0.8 s** |
| **ffmpeg full decode validation** (`-xerror -f null -`, `-threads 4`) | 2.36 s / 120 s of video = 51x realtime | **~71 s** |
| copy to the destination volume | 0.06 s / 53 MiB | ~0.4 s |
| **normal path, total** | | **~74 s + download** |
| if the lesson is EVC-protected: probe + lossless re-encode + a second full validation | NVENC lossless 7.81 s / 60 s of video | **+ ~9 minutes** |

## Findings

1. **The Rust half of an export is about two seconds, or 3% of the non-network time.** Removing both
   redundant `valid_cipher` passes and parallelising the merge is worth ~1.3 s of a ~74 s lesson. It
   is cleanup, not performance work, and it should not be sold as anything else.

2. **The dominant local phase is the full decode validation in `verified_media::publish` (~71 s)**,
   and it is the product's central promise — "publish MP4/MKV only after matching VOD duration and
   decoding all audio/video streams". It is not waste and it is not removable without changing what
   the tool guarantees.

3. **That phase does not need the machine.** `-threads 4` to `-threads 12` buys 8% (2.56 s to 2.35 s
   on the same clip): the decode is bound by something other than the four threads it is given, so
   most of a 12-thread CPU is idle during the phase that is 97% of the local cost.

4. **The window exports one lesson at a time by default, and the setting is the lever.**
   `DEFAULT_WORKERS = 1` is deliberate and tested (`a_fresh_install_exports_one_video_at_a_time`),
   for the EVC reason in finding 5, but the window's settings field takes 1-16 and a development
   machine here already runs `workers: 3, jobs: 6`. Downloading and validating are bound by different
   resources, so overlapping lessons is the largest available win on a batch — and it needs no change
   to any phase. The cost of raising it is a batch whose publishes contend: three lessons each asking
   ffmpeg for four decode threads is twelve threads on six cores, so publishing becomes the batch's
   ceiling even though no single publish uses the machine.

5. **The one phase that is genuinely expensive is the EVC repair path**, and it is 10x the normal one:
   the rawvideo pipe alone moves ~715 MiB/s, so a 60-minute lesson costs ~8 minutes of lossless
   re-encode plus a second 71 s validation. It is triggered by the first full validation failing, and
   a descriptor's `evc_val` cannot be trusted to predict it (see `evc_media::find_context`). This is
   where an unexpected "why did that lesson take ten minutes" comes from.

## Solution

Nothing in the merge or the validation passes. In order of the measurement:

1. **Run more than one lesson at a time, and know what it costs.** The window already has the field
   (`workers`, 1-16); the default of 1 is a deliberate responsiveness choice, not a limit. Raising it
   is the only change with a measured reason to expect a multiple, and it is a setting rather than a
   mechanism — so it should be tried and measured before any code is touched.
2. **Keep NVENC reachable.** The EVC path falls back to `libx264 -crf 0` when `ffmpeg -h
   encoder=h264_nvenc` fails, which is ~2x slower still (16.6 s against 7.8 s per minute of video).
   Both ffmpeg builds on this machine have it; a trimmed build on `PATH` would not.
3. **Leave `full_export::run`'s redundant passes alone** unless the file is being touched anyway. If
   it is: the two `valid_cipher` sweeps can be one, and the merge can call the `decode_ev_parallel`
   that already exists and is already tested. Together they are ~1.3 s per lesson.

## Out of Scope

- Removing or narrowing the full decode validation. It is the promise the tool makes, and every
  alternative considered (hardware decode, keyframe-only, prefix-only) weakens what a published file
  is known to be.
- Optimising `cargo` profiles, AES or SHA-256. At 1.2-2.3 GiB/s none of them is the constraint.
- The download path's own concurrency (`--jobs`, default 8). It is the largest unmeasured term and it
  needs a live session to measure; nothing here should be changed on a guess.

## Further Notes

`crates/evmedia-core/examples/merge_bench.rs` is the instrument, not a product path. It exists so the
next person asking this question gets the table instead of an argument, and it should be deleted when
the question stops being asked — the same rule the research bench follows.

The measurement that matters most is the one this spec does not have: how long 340 MiB actually takes
to arrive. If downloads are the whole story on a real link, item 1 is the only lever that matters and
`--jobs` is the next one to look at; if they are fast, the two changes above are the whole of it.
