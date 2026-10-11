# Why each rule is what it is

Measured against the real bucket and account this skill was built on (2026-10): 1379
objects under `videos/`, 508 稿件, 2271 parts, 443 export workspaces, 213 published videos.

## Contents

- [R2 proof for a published video](#r2-proof-for-a-published-video)
- [Why the workspace rule uses the report instead](#why-the-workspace-rule-uses-the-report-instead)
- [Why a title is not proof](#why-a-title-is-not-proof)
- [Why duration alone is never enough](#why-duration-alone-is-never-enough)
- [The renamed part that stays on disk](#the-renamed-part-that-stays-on-disk)
- [Bilibili risk control](#bilibili-risk-control)
- [What a workspace is](#what-a-workspace-is)
- [Between verification and deletion](#between-verification-and-deletion)
- [What is deliberately never done](#what-is-deliberately-never-done)

## R2 proof for a published video

An object counts only when its **bytes** match, not its name. Same name plus same size is
not enough: a re-export of the same lesson has the same name and can land within a byte or
two of the old size.

- a single-PUT object's ETag is the MD5 of the file;
- a multipart object's ETag is `MD5(concatenated part digests)-N`.

Both digests are computed in **one pass** over the local file. A `-N` ETag is first tried
with 8 MiB parts (what `tools/upload_to_r2.py` uploads with) and then 5 MiB (rclone's
default), because an uploader with a different part size produces a different `-N` ETag for
identical bytes. When neither matches, the file is left alone with `same size, different
bytes` - verify out of band and use `--size-only` only if the bucket's part size cannot be
determined.

Hashing 68 GB takes minutes; results are cached in `content-hashes.json` keyed by path,
size and mtime. Delete that file if hashes are ever suspected.

## Why the workspace rule uses the report instead

A workspace holds decrypted `.ts` segments and a merged `lesson.ts`; R2 holds the remuxed
`.mp4`. Their bytes can never be equal, so byte matching is impossible by construction.
What the workspace does carry is `report.json`, written by the exporter, whose `output` is
the published path it merged into, `bytes` its exact size, `output_seconds` its duration and
`status` whether the merge finished:

```json
{"status": "complete", "output": ".../out/前端架构课程/前端工具链/第一章 课程概述/01. 课程概述.mp4",
 "bytes": 180724298, "output_seconds": 2341.407347, "segments": 234}
```

`bytes` was checked against the published file for every workspace whose published file was
still on disk when this was measured (212 of the 221 with reports that day; the rest had
already been cleaned up): it matched every time, so it is the object's size. That makes the
workspace proof `name + exact size` on R2 plus `name + duration` on Bilibili, and no
hand-written course-to-prefix table is needed - which is what makes this portable to another
machine.

The workspace rule is weaker than the byte rule on purpose, and only there: it cannot tell
two same-sized objects of the same name apart. Both other facts (the report's status and the
Bilibili duration) still have to hold, and the export that wrote the report had the merged
`.ts` on disk to compare durations against.

A workspace whose report is missing (`status` unknown), not `complete`, or whose `bytes` /
`output_seconds` are not usable numbers is never deleted.

## Why a title is not proof

Inside one 稿件, chapters repeat part titles: `BV1zqhb6bE1W` carries `06. APIs` twice, at
2729 s (第三章 ESLint) and 1526 s (第四章 Babel). Keeping one duration per title compared
the other chapter's file against the wrong part, and a file that *was* uploaded read as
missing. Every course also numbers its lessons from `01`, so `01. 课程简介` exists in many
collections. The lookup therefore keeps every duration seen for a title and accepts any
match within tolerance.

## Why duration alone is never enough

Measured over 2269 distinct parts: **1945 (85.7%) share their duration within ±3 s with at
least one other part**. A duration-only fallback would delete files whose Bilibili copy does
not exist. It was designed, measured and **rejected** - do not add it:

```
parts within 3s of 2341.4s:
   BV1jtaP6pEc5   2341s  '02.国际化的技术方案'
   BV1zqhb6bE1W   2342s  '前端工具链'
   BV1K9hp6WEyX   2344s  '19. [应用设计]请求层与状态管理实践'
   BV1HLpG6cE1F   2344s  '15. Nacos API 接口'
```

## The renamed part that stays on disk

Bilibili's CLI and API have no way to rename a part; only the web editor does. In this
account `BV1zqhb6bE1W`'s first part was overwritten with the collection's own name
(`前端工具链`, 2342 s) instead of `01. 课程概述` (2341.4 s). Its local file, and the
workspace that produced it, therefore stay forever with the reason
`no B站 part with this title and duration` - the tool cannot tell that case apart from
"never uploaded", because 4 parts sit within 3 s of that duration. Renaming the part in
Bilibili's own editor makes both deletable on the next run.

## Bilibili risk control

`member.bilibili.com/x/web/archives` answers **HTTP 412** after a burst of calls (observed
after ~76 requests in two minutes, and again on the parts endpoint with 8 workers). A 412,
or `code -702`, means "no answer", never "nothing there": reading an empty listing as an
empty account is how an uploader decides everything needs re-uploading.

- requests are serial with a minimum interval (`bilibili.min_interval`, default 0.3 s);
- a refusal is retried with a 20 s, 40 s, 60 s backoff;
- a 稿件 that still will not answer aborts the run before anything is deleted
  (`--allow-partial-bili` overrides, and should only be used with the user's agreement);
- the 稿件 list is always read fresh - the run refuses to start if it comes back empty;
- part lists are cached (`bili-parts.json`, `bilibili.parts_cache_minutes`, default 12 h) and
  the cache is only a **candidate selector**: it decides which 稿件 are worth re-reading, and
  every 稿件 an artifact's deletion rests on is then read live (phase 2 of a run). A cached
  list can be wrong in both directions - it misses a part appended since it was written (the
  file stays, safe) and it remembers a part edited or deleted since (which would delete a
  local copy Bilibili no longer holds). Only the second one matters, and the live re-read is
  what answers it: an artifact whose part does not come back is kept with
  `B站 no longer holds this part (the cached list was stale)`.

Two sources, by `bilibili.source` (default `auto`):

- `biliup` - `biliup show <bv>`, which kept answering (0.3 s per 稿件) while the raw member
  API was returning 412;
- `api` - `member.bilibili.com/x/vupre/web/archive/view?bvid=`, the only endpoint found that
  returns parts for a 稿件 that is not public. `api.bilibili.com/x/web-interface/view`
  answers `code 62012` and `x/player/pagelist` answers `-404` for those.

## What a workspace is

A directory holding any of `manifest.json`, `list.json`, `original.m3u8`, `report.json`,
`report-mp4.json`, or a `*.evs` file (override with `workspace_markers`). Without that rule
the decrypted segments inside an interrupted export are scanned as published videos: 6856
`.ts` files under `verify_fresh/work` read as "videos R2 does not have".

A directory with segments and no report is an interrupted export, reported as `workspace
with no export report` and left alone - it is the only copy of that lesson's decrypted
segments.

## Between verification and deletion

A proof describes the bytes that were on disk when it was taken. Hashing a large collection
takes minutes, and this project runs uploaders and watchdogs beside it, so a file can be
replaced inside that window. Each artifact therefore carries its size and mtime from the
moment it was scanned, and both are compared again immediately before it is removed. A
mismatch keeps it with `changed after it was verified (size or mtime moved)`; a file that has
already disappeared counts as done, not as a failure (`already removed by another pass`).

`scripts/selftest.py` exercises exactly this: it fakes both platforms, proves four artifacts,
rewrites two of them between verification and deletion, and asserts that the rewritten two
survive while the other two are removed.

## What is deliberately never done

- Nothing is deleted on Bilibili or in R2. Both platforms holding the video is the entire
  reason the local copy may go.
- Nothing outside the configured roots is ever removed; artifacts are discovered by
  scanning those roots.
- A run never starts if the bucket lists nothing under the prefix, or if the account's 稿件
  list comes back empty - both would look like "nothing is uploaded" and leave every file
  in place, or worse, be trusted by a later step.
- `--apply` is required to delete anything. The default is a report.
