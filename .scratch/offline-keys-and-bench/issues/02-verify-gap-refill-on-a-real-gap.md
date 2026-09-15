# 02 — Verify gap detection and refill on a real gap

**What to build:** A real capture in which the playhead is deliberately jumped forward — the way gaps actually arise — leaving a *gap* behind it, and the sweep finds that gap and walks back over it with no help. Ends in a *complete merge*. The fixture tests prove the logic; this proves the logic against a player that is actually rendering.

**Blocked by:** 01

**Status:** ready-for-human

- [ ] A lesson is open and the playhead is jumped forward far enough to leave a gap behind it.
- [ ] The sweep reports detecting the gap and aiming a seek at it.
- [ ] The merge at the end is complete, with no *lesson index* left missing.
- [ ] The command used and its output are recorded as the evidence.

## A run happened, and the artifacts are on disk

`verify_out/` exists with `enc/` holding **nine downloaded segments** (11 MB, timestamps clustered in
one minute) and `dec/` **empty**. There is no `lesson.ts`, no `lesson.partial.ts` and no `keys.json`.

What that establishes: the download path ran and fetched nine signed URLs, and **no key was ever
derived for any of them**, so nothing was decrypted and nothing was merged. `grab`'s own defaults
would not have stopped this early — `--poll 6` with `--idle-limit 60` is six minutes of idle before it
gives up — so the run was stopped by hand about a minute in.

What it does **not** establish, and what this ticket still needs: whether the sweep reported detecting
a gap. That is a line of console output, and it is the thing the ticket is actually for. The artifacts
cannot answer it — a gap that was detected and swept back onto, and a gap that was never noticed,
leave exactly the same two directories behind.

**One correction to the instructions that produced this run.** They said to start `grab` and then jump
the playhead forward. That gets the download side exercised, but nine segments with zero keys means
the player had not decrypted them at the moment they were polled — so the run needs to begin with the
lesson **playing through** a segment or two first, so there is a live key window for the sweep to
compare a gap against, and only then jump forward to create the gap. Without a key window there is
nothing for `seen - ok` to be a gap in.

## Second run: the gap was detected and aimed at, and the seek could not be delivered

`verify_out3/`, `target\release\evmedia.exe grab --pid 6684 --output verify_out3 --mp4`, about twenty
minutes. This run began with the lesson playing, so it is the first one with a key window, and it
answers the question the artifacts alone could not:

```
  keys=70    urls=71    segments=70    done=54    failed=1    new=70 unreachable=1 elapsed=386s
  caught up with the player; keep playing to reveal more segments
  keys=71    urls=45    segments=70    done=54    failed=1    new=0  unreachable=0 elapsed=429s
  ...
  stalled 5 poll(s) with a gap behind the playhead; sweeping back to index 166
  player is holding no segment window; nothing to aim at
  sweep unavailable; waiting for manual playback instead
  ...
  no progress for 60 polls; stopping
  merged 54 segment(s) -> verify_out3\lesson.partial.ts  [INCOMPLETE]
  refusing to remux an incomplete merge; play the rest and rerun (segments are cached)
```

Against the acceptance criteria:

- **A lesson is open and the playhead jumped forward leaving a gap.** Yes — `a gap behind the
  playhead` is the sweep's own words.
- **The sweep reports detecting the gap and aiming a seek at it.** Yes, and this is the criterion the
  first run could not answer: `sweeping back to index 166` is the detection *and* the aim.
- **The merge is complete, with no index missing.** No. 54 segments merged, `[INCOMPLETE]`, and
  `--mp4` correctly refused to remux a partial merge rather than write a broken file.
- **The command and its output are recorded.** Yes, above.

So gap detection and aiming work against a real gap. The failure is one step later and is now named:
**`player is holding no segment window; nothing to aim at`** — the sweep computed the target index
correctly and then had nothing to seek, because the player had released its segment window by the time
the sweep ran. That is a different defect from the one this ticket was opened for, and it is the next
thing to look at.

Two smaller facts worth carrying: `done=54` means the key path is working end to end — 54 segments
decrypted and merged, where the first run decrypted none. And `failed=1` persisted for the whole run
with no reason printed, because the default human reporter discards segment-failure events; the next
run should pass `--json-events` so the failure is on the record.
