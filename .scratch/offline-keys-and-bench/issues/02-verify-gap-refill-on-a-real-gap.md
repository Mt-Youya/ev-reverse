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
