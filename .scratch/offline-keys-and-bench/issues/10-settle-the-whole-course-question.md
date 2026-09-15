# 10 — Settle the whole-course question

**What to build:** The question the whole effort exists for, answered: can a lesson be captured without playing it? It follows from tickets 06, 08 and 09. Either a path exists and is described end to end, or it does not and the goal is restated honestly along with what the tool actually costs per lesson.

**Blocked by:** 06, 08, 09

**Status:** done — a no-playback path exists

- [x] A direct answer: a no-playback path exists, or it does not.
- [x] If it exists, the path is described end to end.
- [x] If it does not, the goal is restated and the real cost per lesson is stated.
- [x] The answer is recorded where the next reader will find it.

## Yes, and it is short

A segment key is `MD5_hex(tk + filename + "20220507")` (ticket 09). `tk` arrives in the same
segment-list response as the signed URL, so **one list response plus the ciphertext is the entire
requirement**:

```
evmedia derive --playlist list.json --input <segments> --output manifest.json
evmedia decode-ev <segments> manifest.json lesson.ts
```

Run for real on a lesson whose list had been captured from the wire and whose ciphertext was
already in the player's download directory, with **no player running and no memory read**: the
merge came out as 18,854 MPEG-TS packets with 18,854 sync bytes, and `ffprobe` reads H.264
2992×1682 plus AAC from it.

So the cost per lesson is no longer a playback pass. It is one API response and one download.

## What that does *not* yet mean

`derive` consumes a list; it does not fetch one. The list comes from `en2v4.ieway.cn` with a bearer
token and an encrypted response body (`docs/API.md`), so a tool that fetches lists by itself is
still gated on ticket 06's response decryption. Until that lands, the list comes from
`tools/parser-tools/capture_all.py`, which reads it inside the player — one click, no playback.

The same Bridge mechanism that returns `20220507` also returns a 32-character value that `0x42A30`
uses as an AES key for an API response, and `tools/parser-tools/probe_kdf.py` generalises to it.
That is the shortest route to closing ticket 06, and with it the last step that still needs the
player.

## Honest statement of the old cost, kept for the record

Everything before ticket 09 said a lesson cost one pass of the playhead, roughly 12 segments per
second when swept and about 1.35 while watching — and that the keys existed only in memory at the
moment of decryption. That was true of every *observation* that had been made, and false as a
statement about the scheme: the keys were a pure function of the wire plus one constant nobody had
read. The lesson is the one ticket 09 already records — a negative is worth exactly as much as the
tool that produced it.
