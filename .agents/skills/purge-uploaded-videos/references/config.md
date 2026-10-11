# Configuration, credentials and artifacts

## Contents

- [Where the config lives](#where-the-config-lives)
- [Keys](#keys)
- [R2 credentials](#r2-credentials)
- [Bilibili session](#bilibili-session)
- [Files the tool writes](#files-the-tool-writes)
- [Exit codes](#exit-codes)
- [Worked examples](#worked-examples)

## Where the config lives

Read in this order; the first one that exists is used.

| Order | Path |
|---|---|
| 1 | `--config FILE` or `PURGE_UPLOADED_CONFIG` (an explicit path that does not exist is an error) |
| 2 | `config.json` next to `SKILL.md` - what a repository carrying the skill commits |
| 3 | Windows `%APPDATA%\purge-uploaded-videos\config.json`; macOS/Linux `${XDG_CONFIG_HOME:-~/.config}/purge-uploaded-videos/config.json` |

`--init` writes a starter to the per-machine path (3). With no config at all the tool falls
back to `./verify_fresh/out` and `./verify_fresh/work` when they exist, which is the export
layout this skill grew out of; it prints that it did.

Paths accept `~` and environment variables. A relative `roots` entry resolves against the
working directory, so a config shipped with a repository expects the tool to be run from the
repository root. Roots may be given per run with `--root DIR` (repeatable), which replaces
the configured ones.

## Keys

```json
{
  "roots": ["D:/exports/out", "D:/exports/work"],
  "r2": {
    "bucket": "cyrus-media-private",
    "prefix": "videos/",
    "account_id": "b48fdde62898699d8398a64c19217cd4",
    "credentials_file": "~/.config/purge-uploaded-videos/credentials.json",
    "cred_reader": "D:/Code/ev-reverse/build/CredRead.exe",
    "cred_target": "cf-evmedia-r2"
  },
  "bilibili": {
    "source": "auto",
    "cookies": "~/.biliup/cookies.json",
    "biliup": "~/.biliup/bin/biliup.exe",
    "min_interval": 0.3,
    "retries": 3,
    "parts_cache_minutes": 720
  },
  "min_age_minutes": 60,
  "duration_tolerance": 3.0,
  "video_suffixes": [".mp4", ".mkv", ".ts"],
  "workspace_markers": ["manifest.json", "list.json", "original.m3u8"],
  "cache_dir": "",
  "ffprobe": ""
}
```

| Key | Meaning |
|---|---|
| `roots` | directories to scan. A video file is an artifact; so is a directory that looks like an export workspace |
| `r2.prefix` | where objects live. Default `videos/`. The whole prefix is listed once per run |
| `r2.account_id` | Cloudflare account id, the `<id>` in `https://<id>.r2.cloudflarestorage.com` |
| `bilibili.cookies`, `bilibili.sessdata`, `bilibili.biliup` | where the session and the binary are. See [Bilibili session](#bilibili-session) |
| `bilibili.min_interval` | seconds between two requests to Bilibili (default 0.3). Lower it and risk 412 |
| `bilibili.retries` | attempts per 稿件 before it counts as unanswered (default 3, with a 20/40/60 s backoff) |
| `bilibili.parts_cache_minutes` | how long a cached part list stays usable as a candidate selector (default 720). `0` disables the cache |
| `min_age_minutes` | leave anything modified more recently alone (default 60). An export may still be writing it |
| `duration_tolerance` | seconds a local and a Bilibili duration may differ (default 3) |
| `video_suffixes` | what counts as a video file (default `.mp4`, `.mkv`, `.ts`). Names containing `.partial.` or `.part.` are always skipped |
| `workspace_markers` | files that mark a directory as an export workspace. `report.json`, `report-mp4.json` and `*.evs` are always included |
| `cache_dir` | where the caches and the report go. Default `%LOCALAPPDATA%\purge-uploaded-videos` on Windows, `~/.cache/purge-uploaded-videos` elsewhere |
| `ffprobe` | explicit ffprobe path (the `FFPROBE` environment variable works too). Without either, the tool looks on `PATH`, then reads MP4 headers directly |

`r2.account_id`, `r2.credentials_file`, `r2.cred_reader`, `r2.cred_target`, `bucket` and
`credentials_file` are also read at the top level of the config, so a two-line config works:
`{"roots": [...], "bucket": "...", "account_id": "..."}`.

## R2 credentials

Looked up in this order; the first complete pair wins. Nothing is written to disk or printed.

1. **Environment** - `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`.
2. **`credentials_file`** - JSON:
   ```json
   {"account_id": "...", "access_key_id": "...", "secret_access_key": "..."}
   ```
   or an AWS-INI style file (`aws_access_key_id=`, `aws_secret_access_key=`, `account_id=`).
3. **OS keyring helper** - `cred_reader` (or a `build/CredRead.exe` found in the working
   directory or one of its parents) invoked as `<helper> <cred_target>_ACCESS_KEY_ID` and
   `<helper> <cred_target>_SECRET_ACCESS_KEY`, the value on stdout. On Windows this is
   `build/CredRead.exe` from the ev-reverse repo, reading DPAPI-encrypted entries.

Per-OS recipes for machines without a helper - export to the environment for one command,
never into the config:

```bash
# macOS keychain
security find-generic-password -s r2-access -w   # -> R2_ACCESS_KEY_ID
security find-generic-password -s r2-secret -w   # -> R2_SECRET_ACCESS_KEY

# Linux (gnome-keyring / libsecret)
secret-tool lookup service r2 key access_key_id
secret-tool lookup service r2 key secret_access_key
```

`--check` prints which source was used without revealing the values.

## Bilibili session

`SESSDATA` comes from `BILI_SESSDATA`, then from a cookie file: `bilibili.cookies`, then
`~/.biliup/cookies.json`, `~/.config/biliup/cookies.json`, `./cookies.json`. Three shapes are
understood - biliup's `{"cookie_info": {"cookies": [...]}}`, a bare list of cookie objects,
and Netscape `cookies.txt`. `biliup login` writes the first, which is the easy way to get one.

`bilibili.source`:

- `auto` (default) - `biliup` when a binary is found on `PATH` or in `~/.biliup/bin`, else
  the HTTP API;
- `biliup`, `api` - force one. See `evidence.md` for what each can read and why the binary
  is preferred.

## Files the tool writes

| File | Purpose |
|---|---|
| `bili-parts.json` | part lists per 稿件, usable for `parts_cache_minutes` as a candidate selector. Safe to delete; `--refresh-bili` ignores it |
| `content-hashes.json` | MD5 and multipart ETag per local file, keyed by path, size and mtime. Written after a dry run too, so the following `--apply` does not re-hash gigabytes. Safe to delete |
| `last-report.json` | every artifact, its decision and the evidence behind it, plus the platform summary |

The report is the audit trail: each entry carries `title`, `duration`, `duration_source`,
`size`, `decision`, `reason` and a `detail` holding the R2 key and the Bilibili `bvid` that
proved it. `decision` is written at the end of the run, so after `--apply` it reads `deleted`,
`delete-failed`, `changed after it was verified`, `gone`, or `keep` with a reason. The
top-level `deleted` block repeats the counts together with `changed_since_verified`,
`already_gone` and `failed`.

A dry run and an applied run write the same file; `mode` says which one it is, and the last
write is authoritative.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | the run finished. In dry-run mode that means a report was produced; with `--apply` it means no deletion failed (artifacts that changed since they were verified are not failures - they were left alone) |
| 1 | the run refused to start (no credentials, empty bucket, unreadable 稿件, no roots), or some deletions failed - the message names them |

## Worked examples

The setup this skill was built against (a remote-access layout, no secrets in the config):

```json
{
  "roots": ["D:/Codes/github/ev-reverse/verify_fresh/out",
            "D:/Codes/github/ev-reverse/verify_fresh/work"],
  "r2": {"bucket": "cyrus-media-private", "prefix": "videos/",
         "account_id": "b48fdde62898699d8398a64c19217cd4", "cred_target": "cf-evmedia-r2"},
  "bilibili": {"source": "auto"},
  "min_age_minutes": 60
}
```

A portable one, credentials in the environment and cookies in a file beside it:

```json
{
  "roots": ["/data/exports/published", "/data/exports/scratch"],
  "r2": {"bucket": "my-media", "prefix": "videos/", "account_id": "0123456789abcdef"},
  "bilibili": {"source": "api", "cookies": "~/.config/biliup/cookies.json"}
}
```

```bash
export R2_ACCESS_KEY_ID=...  R2_SECRET_ACCESS_KEY=...
python3 scripts/purge_uploaded.py --check
python3 scripts/purge_uploaded.py --root /data/exports/published
```
