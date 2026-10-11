---
name: purge-uploaded-videos
description: Delete local videos that Bilibili (B站) and Cloudflare R2 both already hold, so disk space is freed without losing anything. Every file must pass two independent proofs before it is removed - R2 holds an object with the same bytes (size plus MD5 against the ETag, multipart ETags reproduced locally), and one of the account's 稿件 carries a part (分P) with the same title whose duration matches. Export workspaces are verified through the report.json of the export that produced them, so no hand-written course table is needed. Dry run by default; nothing remote is ever deleted. Windows, macOS and Linux, standard library only - ffprobe and biliup are used when present, never required. Use when asked to 删除本机已经传过 B站 和 R2 的视频, to 清理已上传课程的本地副本 and free disk space, to 检查哪些本地视频还没传到 B站 或 R2, or after bulk uploads when the local exports are redundant. Do not use it to delete anything from Bilibili or R2.
---

# Purge uploaded videos

Remove the local copy of a video once **both** platforms demonstrably hold it. The whole
value of this skill is the strength of "demonstrably": a name match is not proof, and a
wrong deletion cannot be undone.

## What counts as proof

| Artifact | R2 proof | Bilibili proof |
|---|---|---|
| a published video file | an object with the same name whose **bytes** match: size, then MD5 against a single-PUT ETag, or the MD5-of-part-MD5s against a `-N` multipart ETag | a part (分P) of one of the account's 稿件 with the same normalized title whose duration is within `--duration-tolerance` (3 s) |
| an export workspace directory | the object that export's own `report.json` names (`output`), by name and **exact size** (`bytes`) | the duration the same report recorded (`output_seconds`) |

Two asymmetries are deliberate; `references/evidence.md` has the failure mode behind each.

- A workspace holds decrypted `.ts` segments while R2 holds the remuxed `.mp4`, so bytes can
  never match there. Its proof is the report's own output name and size. A workspace with no
  `report.json` is an interrupted export and is never touched.
- A title without a duration is never proof: chapters repeat part titles inside one 稿件, and
  every course numbers its lessons from `01`.

An unanswered platform is an error, never an answer: Bilibili answering 412 (risk control) or
a bucket that lists nothing **aborts the run** instead of reporting "not uploaded".

Cached part lists only *select* which 稿件 matter. Every 稿件 a deletion rests on is re-read
live in the same run before anything is removed: a part edited or deleted since the cache was
written would otherwise look like Bilibili still holding the video. For the same reason each
artifact is re-checked (size and mtime) immediately before it is removed - hashing tens of
gigabytes takes minutes, and an export can rewrite a file inside that window.

## Where the config comes from

`--config FILE` or `PURGE_UPLOADED_CONFIG` wins. Otherwise the tool reads, in order:

1. **`config.json` next to this SKILL.md** - what a repository that carries the skill
   commits, so the skill works where it is checked out with no per-machine setup. A
   relative `roots` entry resolves against the working directory, so **run the tool from
   the repository root**.
2. **The per-machine config** - `%APPDATA%\purge-uploaded-videos\config.json` on Windows,
   `~/.config/purge-uploaded-videos/config.json` elsewhere. `--init` writes a starter there.

The minimum is `roots`, `r2.bucket` and `r2.account_id`. `references/config.md` lists every
key, the credential sources (environment, JSON/INI file, OS keyring) and per-OS recipes. R2
credentials never live in the config and are never printed.

```bash
# prove that the config, the credentials and both platforms answer
python3 scripts/purge_uploaded.py --check
```

## Workflow

```bash
# report only: what would go, what stays, and why. Nothing is deleted.
python3 scripts/purge_uploaded.py

# narrow it down while testing
python3 scripts/purge_uploaded.py --only 前端架构课程 --limit 5 --verbose

# delete what passed both proofs
python3 scripts/purge_uploaded.py --apply

# machine-readable, for a follow-up step or a summary
python3 scripts/purge_uploaded.py --json
```

1. **Run the dry run first and show the user its summary** - counts, gigabytes, and the
   "left alone" reasons. `--apply` is irreversible and no harness will ask for confirmation
   on the user's behalf.
2. Then re-run with `--apply`. It prints every deleted artifact and the bytes freed.
3. Report the JSON report path (`last-report.json` in the cache directory, or `--report FILE`)
   so any single decision can be audited afterwards.

Hashing is the expensive half (68 GB of video takes minutes the first time). Digests are
cached by path, size and mtime, so later runs take seconds.

## Options that change the outcome

| Option | Effect |
|---|---|
| `--min-age-minutes N` | leave artifacts modified in the last N minutes alone (default 60). An export may still be running, and deleting its workspace throws that work away |
| `--bili-source auto\|biliup\|api` | how to read the account's parts. `auto` prefers the `biliup` binary, which kept answering while the raw member API answered 412; `api` is for machines without it |
| `--refresh-bili` | ignore the cached part lists and re-read every 稿件 in the index, not only the ones a deletion rests on. A cache hit can hide a newly appended part, never invent one |
| `--allow-partial-bili` | delete despite 稿件 Bilibili would not answer for. Only with the user's explicit agreement: an unread 稿件 may be the only place a video is held |
| `--size-only` | accept an object of the same name and size as the R2 proof **for a published video** - weaker, it skips the content hash. For buckets uploaded with an unknown multipart part size. A workspace is always name plus exact size |
| `--limit`, `--only` | inspect fewer artifacts; `--only` matches any substring of the path and can be repeated |

After editing any script, `python3 scripts/selftest.py` runs the whole pipeline against two
faked platforms in a temp directory: it proves the guards still hold without a network or a
real video.

## Reading the result

The console groups each artifact as `delete` or as one of the "left alone" reasons:

```
no B站 part with this title and duration   the upload never happened, or the part was renamed
B站 no longer holds this part              the cached part list was stale; the live re-read disagreed
R2 has no object with this name            the upload never happened, or the prefix is wrong
workspace with no export report            an interrupted export; never touched
modified N min ago (< 60)                  possibly still being written
R2: same size, different bytes             a same-named object holds something else
```

With `--apply` an entry ends as `deleted`, or as `changed after it was verified`, `already
removed by another pass` or `delete-failed` - the report says what happened, not what was
planned. Only `delete-failed` makes the run exit non-zero.

When a whole collection reads as "R2 has no object with this name", suspect `r2.prefix`
before suspecting the uploads. The run refuses to start on a bucket that lists nothing, but a
prefix that lists *something else* still looks like a successful run.

## References

- `references/evidence.md` - the exact rules, the failure modes behind them, and the
  Bilibili/R2 behaviour worth knowing (412 risk control, which endpoints answer, why a cached
  part list is only a candidate selector).
- `references/config.md` - full config schema, credential sources, per-OS keyring recipes,
  cache files and exit codes.
