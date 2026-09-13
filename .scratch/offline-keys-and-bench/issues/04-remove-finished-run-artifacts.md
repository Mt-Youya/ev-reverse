# 04 — Remove the artifacts of finished runs

**What to build:** Disk stops carrying the leavings of runs that are over: the partial merge, the decrypted-segment directories, the concatenated intermediate files. The two finished videos and the capture evidence stay. This is disk hygiene only — every one of these paths is outside version control, so nothing here changes the repository.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [x] The partial merge and the concatenated intermediate files are gone.
- [x] The decrypted-segment directories, which can be rebuilt from the player’s cache, are gone.
- [x] Both finished videos are intact.
- [x] The capture evidence directory is intact.

## What went

Roughly 1.6 GB, all of it under `.gitignore` (`rust_out/`, `tools/parser-tools/ev2_out/`), so the
repository is untouched.

**Concatenated intermediates and the partial merge** — the merge step builds a whole-lesson `.ts` and
then remuxes it, so every finished video left its own `.ts` behind. Removed:

| Path | Size |
| --- | --- |
| `rust_out/final/lesson-1113723/lesson.ts` | 383 MB |
| `rust_out/lesson.ts` | 209 MB |
| `tools/parser-tools/ev2_out/c628090a-1c0.partial.ts` | 156 MB (the partial merge) |
| `tools/parser-tools/ev2_out/c628090a-1c0.ts` | 2 MB |

**Decrypted-segment directories** — `dec/` holds plaintext segments and `enc/` holds the ciphertext
they came from; both are rebuilt from the player's own cache on the next run, and `grab` resumes from
whatever it finds, so removing them only costs the re-run.

| Path | Size |
| --- | --- |
| `rust_out/final/lesson-1113723/dec/` | 368 MB, 476 files |
| `rust_out/dec/` | 201 MB, 259 files |
| `tools/parser-tools/ev2_out/dec/` | 150 MB, 178 files |
| `tools/parser-tools/ev2_out/enc/` | 150 MB, 178 files |

## What stayed

- `rust_out/final/lesson-1113723/lesson.mp4` — 356 MB, untouched.
- `rust_out/lesson.mp4` — 195 MB, untouched.
- `tools/parser-tools/captured/` — all 26 entries, including `bodies.bin` and the per-caller key log
  that tickets 06 and 08 read.
- `tools/parser-tools/ev2_out/c628090a-1c0.mp4` — 1.8 MB. Not one of the two full-length videos, so
  ticket 04 did not call for it and it is not recoverable from git (`ev2_out/` is ignored). Left alone
  rather than deleted on a technicality.
- `tools/parser-tools/ev2_out/keys.json` (40 KB) and `manifests.capture` (220 KB) — small, and they are
  the record of what the Python pipeline managed. Leaving them costs nothing.
- `ctx_dump.json`, `dump_contexts.json`, `triple.json`, `__pycache__/` — experiment dumps that the
  surviving instruments produced and may read again. Not in this ticket's scope.

## Found while verifying, and not caused by this ticket

"Intact" above was first asserted from the file sizes, which is a weak instrument — a size that did
not change says nothing about what is inside. Decoding each video instead:

| Video | h264 decode errors |
| --- | --- |
| `rust_out/final/lesson-1113723/lesson.mp4` | 0 |
| `rust_out/lesson.mp4` | **2381** |
| `ev2_out/c628090a-1c0.mp4` | 0 |

`rust_out/lesson.mp4` is genuinely damaged, and the damage is concentrated: the first 60 seconds
produce 1040 of the 2381 lines, while a middle minute and a final minute produce 2 each. So the
opening minute is broken and the remaining 42 are essentially clean — the shape of a merge whose
first segments are wrong, not of a uniformly bad file.

This is **pre-existing**, and the file timestamps settle it: `lesson.mp4` is from 15:36 and
`final/lesson-1113723/lesson.mp4` from 18:04, both hours before this ticket's deletions ran at 20:44.
Removing a sibling file cannot damage an mp4, and the mtimes confirm nothing was rewritten.

So the ticket's own criterion holds in the sense it was written — the cleanup did not remove or
damage either video, and both are byte-for-byte unchanged. But the discovery is real and is a defect
in a product output, so it is recorded here rather than passed over: `grab`'s merge produced a file
with a damaged opening on at least one run. Splitting it out as its own follow-up.

One consequence worth naming: `rust_out/lesson.ts` was the intermediate this mp4 was remuxed from,
and ticket 04 deleted it. Re-checking the merge now means re-running `grab` against the player's
cache (`D:\Downloads\EVPlayer2Downloads` still holds the ciphertext), not re-reading a file.

