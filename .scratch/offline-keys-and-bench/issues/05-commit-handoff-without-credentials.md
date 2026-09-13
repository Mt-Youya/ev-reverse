# 05 — Put HANDOFF.md in the repository without its credentials

**What to build:** The record of the early rounds belongs in the repository, but not with a live credential in it. The tokens and the complete request sample are replaced with placeholders that keep the shape readable, and the file is committed.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [x] No token, signature or key value survives in the file.
- [x] The placeholders preserve the shape of what they replace, so the record still reads.
- [x] The file is committed and present in the repository.
- [x] A search for the removed values across the tracked tree returns nothing.

## What was redacted

Six values, all of them real material captured from the player. Each was checked against the whole
tree first: they appeared in `HANDOFF.md` and nowhere else that is tracked.

| Position | What it was | Where |
| --- | --- | --- |
| the manifest's `tk` | the per-segment token | the manifest JSON sample |
| the `tk` of the complete triple | the same kind of token | the "完整三元组" sample |
| the `key` of the complete triple | a segment key, already recovered | the same sample |
| the `sign` of both signed URLs | the URL signature | both samples |
| the `t` parameter of both URLs | the other signed query parameter | both samples |
| the course and segment UUIDs | the lesson and segment identity | both samples |

**The values themselves are deliberately not written into this ticket.** They were here first, in
the form of a table listing each value verbatim next to what it was — and `.scratch/` is tracked, so
that table would have put all six back into the repository as the very act of recording their
removal. The positions above are enough for a reader to know what is missing and why; the values are
in `captured/`, which is where they belong. Do not restate them here.

Replaced in `HANDOFF.md` with `<REDACTED-TOKEN>`, `<REDACTED-SIGN>`, `<REDACTED-KEY>` and
`<REDACTED-*-UUID>`, and a note at the top of the file says so — so a reader knows the position held
a real value and knows how to get their own by re-running the capture.

**Where the values still live:** `tools/parser-tools/captured/` — the raw capture, including
`zlib.bin` and `inflated/*.json`. That directory is what `.gitignore:16` excludes, and this ticket is
the reason that rule exists. `git grep` over the tracked tree returns nothing for all six; the
criterion is met, and the evidence stays on disk where the instruments can use it.

## What also had to change for the record to read

The file contradicted itself and referred to files that no longer exist. Both were fixed rather than
left for the next reader:

- The header still claimed goal 2 was "差最后两步（见"当前卡点"）", but the later rounds had already
  run the whole pipeline end to end, and there is no "当前卡点" section. It now states the real
  position: the chain works and requires playing each lesson once, and that requirement is what
  directly contradicts goal 2.
- Section 4 presented `key = MD5(tk + 文件名 + 运行时附加参数)` as "已验证的解密算法" and called the
  salt the only missing piece. The salt does not exist. The section is now marked as a superseded
  snapshot, and the formula is corrected to the one that actually works — the segment's own 32 hex
  characters used directly as the AES-256 key. The mask line and the AES line were already right and
  are unchanged.
- The tail sections pointed at `src/main.rs` (gone since the workspace refactor), and at
  `ev2_batch.py`, `ev2_grab.py`, `find_segments.ps1`, `harvest_keys.py`, `compare_tk.py`,
  `walk_aes.py`, `一键抓取.cmd`, `解密转MP4.cmd` — all of which ticket 03 deleted, or which the
  third round already knew were dead. They are now marked as historical names, with a pointer to
  ticket 03's verdict table.
