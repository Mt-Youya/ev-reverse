# 03 — Classify the research bench and delete what the product already covers

**What to build:** Every script under the research bench gets exactly one verdict — the Rust product already covers it, it is still an instrument, or it cannot work against this build — and the covered and dead ones are deleted rather than described. Instruments are left alone: they are the apparatus tickets 06–10 run on, and rewriting them mid-experiment is the thing to avoid.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [x] Every script has a recorded verdict with a one-line reason.
- [x] Scripts whose capability the Rust product already implements are deleted.
- [x] Scripts that cannot work against this build are deleted.
- [x] Instruments survive untouched, and the list of them is what tickets 06–10 draw from.
- [x] Nothing the product or the remaining experiments depend on is deleted.

## Verdicts

31 Python scripts and one PowerShell script, each with one verdict. Twenty-one files were deleted in
all: eighteen Python scripts, the PowerShell script, and two `.cmd` wrappers. Thirteen Python scripts
survive as instruments.

### Covered by the product — deleted

The capability is now implemented in Rust, and the Rust version is the one that is correct. Every
harvest-family script here tests candidates against segment bytes; the bench's versions all predate
the 752-byte probe alignment fix, so their "0 hits" readings were artefacts of the bug.

| Script | Why it goes |
| --- | --- |
| `scan_ctypes.py` | ReadProcessMemory-only key scan — its own docstring names `evmedia-win/scan.rs` as the target, and that is now what the product does. |
| `fast_scan.py` | Scans memory for 32-hex candidates and tests them against the recently-written segments; that candidate path is `keyscan::recover`. |
| `harvest_all.py` | Memory scan, test against every downloaded segment, write JSON — the same job as `grab` plus `keyscan::recover`. |
| `harvest_keys.py` | Collects every 32-hex string and tries each as a key — superseded by the same path. |
| `scan_keys_all.py` | `harvest_keys.py` with a better target set; superseded the same way. |
| `try_keys.py` | Tests captured keys against real ciphertext — that test is `keyscan::recover`. |
| `ev2_grab.py` | The entire Python pipeline, harvest through merge; the Rust `grab` does all of it, including resume. |
| `drive_playhead.py` | Posts arrow keys to steer the playhead; `evmedia-win::playhead` does this, and its docstring says so. |

### Cannot work against this build — deleted

| Script | Why it goes |
| --- | --- |
| `ev2_batch.py` | Derives keys from manifests with the documented salt formula. That formula does not hold in this build, so without a salt it has no route to a key. |
| `hunt_salt.py` | Searches for the `tk + filename + salt` concatenation in memory. The decisive negative is that this build never builds that string. |
| `compare_tk.py` | Exists only to feed the tk-derivation family, which is the same disproven formula. |
| `find_b64.py` | Locates the base64 decoder by watching alphabet tables; repeated full-memory runs found nothing, and ticket 06 no longer needs the decoder. |
| `aes_map.py` | Static function-boundary mapping of the AES region. **Deleted on a false premise — see the correction below.** |
| `aes_disasm.py` | Same static route to the same RVA. **Deleted on a false premise — see the correction below.** |
| `disas.py` | Same static route again, against a live module; it times out. |
| `walk_aes.py` | Hooks the AES T-table site and walks up to its caller. The callers live in heap-generated code, so the walk reaches nothing — that is the finding, not a bug in the script. |
| `hexdiff.py` | The differential scan is broken (returns in 0.1 s with zero rows, i.e. it never scanned), and its question is now asked directly by ticket 07's write breakpoint, which needs no diff. |
| `_extract.py` | A one-off that pulled text out of a single, long-superseded session transcript into a log. It is not apparatus for anything pending. |
| `find_segments.ps1` | Its RVAs point at this build's M3U8 parser, not at key derivation. It has failed every time it was run. |

### Correction: two of those deletions rested on a premise that is false

`aes_map.py` and `aes_disasm.py` were deleted here as "cannot work against this build", because the
note this project was carrying said static analysis was unavailable: *"静态分析不可用：无
capstone/pefile/dumpbin"*. **That is wrong.** Both `capstone` (5.0.7) and `pefile` (2024.8.26) are
installed in `C:\Users\Yonjay\.conda\envs\subgen`, and `PlayerLibRender56_vs.dll` is **not packed** —
six normal sections and a `.pdata` table with 19,488 function entries. The whole DLL is statically
analysable from the file, and was, in the course of preparing ticket 06: the app's AES key setup
(`0x20EC0`), its block-call trampoline (`0x20EA0`) and the two consumers of the segment-key field were
all located that way without a player.

The likely origin of the false note is the interpreter. The PATH `python` on this machine is the
Microsoft Store placeholder and has neither package; the conda one has both. A note written after
running the wrong `python` reads exactly like an absence of the tool.

The second half of the original reasoning was wrong too: it said the entry points those scripts sought
were "already known", but `0x792c78` is an instruction in the middle of a function, not an entry, and
the correct way to get the entry is `.pdata` — not the `cc cc cc` scan this ticket's sibling suggested.

Both files are recoverable from git (`git show de68f47:tools/parser-tools/aes_map.py`). They were not
restored, because what they did is now done better and directly by `.pdata` plus capstone. But the
verdict recorded against them here was wrong, and the reason above is the record of how.

### Wrappers on the dead offline path — deleted

- `一键抓取.cmd` — exists only to run `find_segments.ps1`.
- `解密转MP4.cmd` — consumes the `evplayer2_manifest.json` that `find_segments.ps1` would have produced, so it cannot run either.

The vendored third-party directories and the two `.zip` archives (`EVPlayer2_解密工具与参数/`,
`EVPlayer2通用解密工具/`, and both zips) are **left in place**. They are not bench scripts, they are
tracked, and the zips are the authoritative copy of the vendor material. They sit on the same dead
offline path as the two wrappers above — worth knowing, but removing someone else's artefact is not
this ticket.

### Instruments — kept untouched

These are what tickets 06–10 draw from. `probe_urls.py` and `capture_all.py` overlap (the latter is
the v6 successor to the former); both are kept, because pruning apparatus mid-experiment is the thing
this ticket exists to avoid.

- **Ticket 06 (the API AES as an oracle):** `probe_at.py`, `hook_aes_key.py`, `find_aes_key.py`, `extract_bodies.py`.
- **Ticket 07 (the instruction that writes a segment key):** `probe_at.py`, `probe_schedule_key.py`, `wait_ready.py`, and `watch_key.py` — the last of which cannot be run as it stands. **Added after this classification, not part of the thirteen:** `tools/parser-tools/probe_write.py`, written when ticket 07 was made ready to run. `watch_key.py` is the only survivor that can arm a memory write watch and it is the one that hangs the player, so waiting on it would have cost the session the ticket exists for. Ticket 07 records what replaced it and why.
- **Ticket 08 (the `getDownEVSKey` plaintext):** `capture_all.py`, `sniff_plain.py`, `fish_api_plain.py`, `probe_urls.py`.
- **Both / general:** `hook_caller.py`, `find_in_mem.py`.

The count is therefore fourteen Python scripts on the bench today, not the thirteen this ticket left. The fourteenth arrived with ticket 07's preparation, after the classification was made.

### Dependency check

Nothing the product or the remaining experiments need was deleted. A repo-wide search for each
deleted name found only: `HANDOFF.md` (historical narrative, handled in ticket 05), `captured/harvest_all.log`
(capture evidence, which stays), and `scan_keys_all.py` inside the harvest family, deleted with it.
`disas.py`'s only other match is `tools/device_analysis/disasm.py`, a different file in a different
directory, untouched.
