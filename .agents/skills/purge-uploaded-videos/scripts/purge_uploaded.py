#!/usr/bin/env python3
"""Delete local videos that Bilibili and Cloudflare R2 both already hold.

Two independent proofs are required before anything is removed:

  * R2       -- the bucket holds an object with the same bytes. For a published video that
                means size plus MD5 against the ETag (`-N` multipart ETags are reproduced
                from the local file), so a same-named object with different bytes never
                counts. For an export scratch directory the proof is the object the
                export's own report names, by name and exact size: scratch holds `.ts`
                segments, R2 holds the remuxed `.mp4`, so bytes can never match there.
  * Bilibili -- a 稿件 of this account carries a part (分P) with the same title whose
                duration matches the local one within a few seconds. A title alone is not
                proof: chapters repeat part titles inside one 稿件 (`06. APIs` twice, at
                2729 s and 1526 s), and different courses number their lessons from 01.

Nothing remote is ever deleted, and a dry run is the default.

    python3 purge_uploaded.py                  # report only
    python3 purge_uploaded.py --apply          # delete what passes both checks
    python3 purge_uploaded.py --check          # validate config, credentials and access
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import pathlib
import re
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.dont_write_bytecode = True      # keep the skill directory free of __pycache__
import backends as bk  # noqa: E402

REPORT_NAMES = ("report.json", "report-mp4.json")
# What a per-lesson workspace leaves behind: an export's own manifest, its signed segment
# list, its playlist, or the descriptor. A directory holding one of these is scratch, not a
# video folder -- without this, every decrypted segment inside an interrupted export reads
# as a published video that R2 does not have (6856 of them in verify_fresh/work alone).
WORKSPACE_MARKERS = ("manifest.json", "list.json", "original.m3u8")
PARTIAL = re.compile(r"\.(partial|part)\.", re.I)
DEFAULT_PREFIX = "videos/"


# --------------------------------------------------------------------------- config


def default_config_path() -> pathlib.Path:
    if os.name == "nt":
        base = pathlib.Path(os.environ.get("APPDATA") or pathlib.Path.home() / "AppData/Roaming")
    else:
        base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
    return base / "purge-uploaded-videos" / "config.json"


def skill_config_path() -> pathlib.Path:
    """A config shipped inside the skill itself: <skill>/config.json.

    This is what lets the skill be committed to a repository and work where it is checked
    out, with no per-machine setup. Paths in it are relative to the working directory the
    tool is run from, so run it from the repository root.
    """
    return pathlib.Path(__file__).resolve().parent.parent / "config.json"


def default_cache_dir() -> pathlib.Path:
    if os.name == "nt":
        base = pathlib.Path(os.environ.get("LOCALAPPDATA")
                            or pathlib.Path.home() / "AppData/Local")
    else:
        base = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache")
    return base / "purge-uploaded-videos"


def expand(value) -> str:
    return os.path.expandvars(os.path.expanduser(str(value)))


def load_config(args) -> tuple[dict, pathlib.Path | None]:
    """The config to run with, and where it came from.

    Order: an explicit `--config` / `PURGE_UPLOADED_CONFIG` wins, then a config shipped in
    the skill directory (so a repository that carries the skill needs no setup), then the
    per-machine config under `%APPDATA%` / `~/.config`.
    """
    explicit = args.config or os.environ.get("PURGE_UPLOADED_CONFIG")
    if explicit:
        path = pathlib.Path(expand(explicit))
        if not path.is_file():
            raise SystemExit(f"config {explicit} does not exist")
    else:
        path = next((candidate for candidate in (skill_config_path(), default_config_path())
                     if candidate.is_file()), default_config_path())

    config: dict = {}
    if path.is_file():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise SystemExit(f"config {path} is not valid JSON: {error}")
        if not isinstance(config, dict):
            raise SystemExit(f"config {path} must hold a JSON object")

    roots = [expand(r) for r in (args.root or [])]
    if not roots:
        # With no config at all, fall back to the export layout of the project this tool
        # grew out of: verify_fresh/out holds published videos, verify_fresh/work the
        # per-lesson scratch each of them was merged from.
        guesses = [pathlib.Path("verify_fresh/out"), pathlib.Path("verify_fresh/work")]
        roots = [str(g) for g in guesses if g.is_dir()] if not config.get("roots") else []
        if roots:
            print(f"no config found: scanning the export layout in {pathlib.Path.cwd()}")
    if roots:
        config["roots"] = roots
    elif config.get("roots"):
        config["roots"] = [expand(r) for r in config["roots"]]

    cache = pathlib.Path(expand(config.get("cache_dir") or default_cache_dir()))
    config["_cache_dir"] = cache
    return config, (path if path.is_file() else None)


# --------------------------------------------------------------------------- scanning


def read_export_report(directory: pathlib.Path) -> dict | None:
    """The export report a scratch directory wrote about the video it merged into.

    The numbers are checked here, not where they are used: a hand-edited or truncated
    report must leave its workspace alone, not abort a run that is otherwise ready to
    delete. `bytes` must be a whole number of bytes and `output_seconds` a duration.
    """
    for name in REPORT_NAMES:
        candidate = directory / name
        if not candidate.is_file():
            continue
        try:
            blob = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
            size = int(blob["bytes"])
            seconds = float(blob["output_seconds"])
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if not isinstance(blob, dict) or not blob.get("output") or size <= 0 or seconds <= 0:
            continue
        blob["_report"] = candidate.name
        blob["bytes"], blob["output_seconds"] = size, seconds
        return blob
    return None


def directory_footprint(directory: pathlib.Path) -> tuple[int, int] | None:
    """(total bytes, newest mtime in ns), or None when the directory is already gone.

    Nothing inside a workspace is hashed - the R2 proof for one comes from its report - so
    the footprint doubles as the "has this changed since it was verified" check that runs
    just before the directory is removed.
    """
    try:
        size, newest = 0, directory.stat().st_mtime_ns
        paths = list(directory.rglob("*"))
    except OSError:
        return None
    for path in paths:
        try:
            info = path.stat()
        except OSError:
            continue
        if path.is_file():
            size += info.st_size
        newest = max(newest, info.st_mtime_ns)
    return size, newest


def unchanged_since_verified(item) -> bool:
    """Is the artifact still the one the proofs were taken from?"""
    if item["kind"] == "video":
        try:
            info = item["path"].stat()
        except OSError:
            return False
        return info.st_size == item["size"] and info.st_mtime_ns == item["mtime_ns"]
    footprint = directory_footprint(item["path"])
    return footprint is not None and footprint == (item["size"], item["mtime_ns"])


def is_workspace(directory: pathlib.Path, markers) -> bool:
    if any((directory / marker).is_file() for marker in markers):
        return True
    return next(directory.glob("*.evs"), None) is not None


def scan_root(root: pathlib.Path, suffixes: tuple[str, ...], markers):
    """(published videos, workspaces) under one root.

    A workspace is an artifact in its own right: its contents belong to the lesson it was
    merging, so nothing inside it is read as a published file. That is what keeps retained
    `.ts` segments from being counted as videos the bucket does not hold.
    """
    videos, workspaces = [], []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError as error:
            print(f"  ! cannot read {directory}: {error}")
            continue
        for entry in entries:
            if entry.is_dir():
                if is_workspace(entry, markers):
                    workspaces.append((entry, read_export_report(entry)))
                else:
                    stack.append(entry)
            elif (entry.is_file() and entry.suffix.lower() in suffixes
                  and not PARTIAL.search(entry.name)):
                videos.append(entry)
    return videos, workspaces


# ------------------------------------------------------------------------ verification


class Hashes:
    """Content digests of local files, remembered across runs.

    Hashing is the expensive half of the R2 proof (68 GB of video takes minutes), and the
    answer cannot change while size and mtime stay the same.
    """

    def __init__(self, cache_file: pathlib.Path):
        self.file = cache_file
        self.entries: dict = {}
        self.dirty = False
        try:
            self.entries = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.entries = {}

    def digests(self, path: pathlib.Path, chunk: int) -> tuple[str, str]:
        info = path.stat()
        key = f"{path}|{info.st_size}|{info.st_mtime_ns}|{chunk}"
        hit = self.entries.get(key)
        if hit:
            return hit["md5"], hit["multipart"]
        whole, multipart = bk.file_digests(path, chunk)
        self.entries[key] = {"md5": whole, "multipart": multipart}
        self.dirty = True
        return whole, multipart

    def save(self) -> None:
        if not self.dirty:
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.entries), encoding="utf-8")
        except OSError:
            pass


def r2_proof(path: pathlib.Path, size: int, candidates, hashes: Hashes,
             size_only: bool) -> tuple[bool, str]:
    """(proven, detail) for a local file against the objects carrying its name."""
    same_size = [key for key, remote_size, _ in candidates if remote_size == size]
    if not same_size:
        names = ", ".join(f"{k} ({bk.human(s)})" for k, s, _e in candidates[:3])
        return False, f"no object of this size ({bk.human(size)}); bucket has {names}"
    if size_only:
        return True, f"size only: {same_size[0]} ({bk.human(size)})"
    for key, _remote_size, remote_etag in candidates:
        if _remote_size != size:
            continue
        if "-" not in remote_etag:
            whole, _ = hashes.digests(path, bk.PART_SIZES[0])
            if whole == remote_etag:
                return True, f"{key} (MD5 {whole[:12]})"
        else:
            for chunk in bk.PART_SIZES:
                _whole, multipart = hashes.digests(path, chunk)
                if multipart == remote_etag:
                    return True, (f"{key} (multipart ETag, parts of "
                                  f"{chunk // bk.MIB} MiB)")
    return False, (f"same size, different bytes (object ETag "
                   f"{candidates[0][2][:20]}...)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="config file (default: the platform config path)")
    parser.add_argument("--root", action="append", metavar="DIR",
                        help="a directory to scan; repeatable, overrides the config's roots")
    parser.add_argument("--apply", action="store_true", help="actually delete; default is a report")
    parser.add_argument("--check", action="store_true",
                        help="validate config, credentials and access, then stop")
    parser.add_argument("--init", action="store_true",
                        help="write a starter config to the platform config path and stop")
    parser.add_argument("--only", action="append", metavar="SUBSTR",
                        help="restrict to paths containing this substring; repeatable")
    parser.add_argument("--limit", type=int, help="inspect at most this many artifacts")
    parser.add_argument("--min-age-minutes", type=float,
                        help="leave artifacts modified more recently than this alone")
    parser.add_argument("--duration-tolerance", type=float, help="seconds, default 3")
    parser.add_argument("--bili-source", choices=("auto", "biliup", "api"),
                        help="how to read the account's 稿件 parts (default auto)")
    parser.add_argument("--refresh-bili", action="store_true",
                        help="ignore the cached part lists and re-read every 稿件")
    parser.add_argument("--allow-partial-bili", action="store_true",
                        help="delete despite 稿件 that Bilibili would not answer for")
    parser.add_argument("--size-only", action="store_true",
                        help="treat an object of the same name and size as proof of R2 "
                             "(weaker: skips the content hash)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--report", metavar="FILE", help="where to write the JSON report")
    parser.add_argument("--verbose", action="store_true", help="print every artifact")
    args = parser.parse_args()

    bk.use_utf8_console()

    if args.init:
        # Before load_config: --init exists to create a config that does not exist yet, and
        # load_config refuses a --config path that is missing or not valid JSON.
        path = pathlib.Path(expand(args.config)) if args.config else default_config_path()
        if path.exists():
            raise SystemExit(f"{path} already exists; edit it instead of overwriting it")
        roots = [str(g) for g in (pathlib.Path("verify_fresh/out"), pathlib.Path("verify_fresh/work"))
                 if g.is_dir()]
        starter = {
            "roots": roots or ["<directory holding the videos>"],
            "r2": {"bucket": "<bucket>", "prefix": DEFAULT_PREFIX,
                   "account_id": "<cloudflare account id>"},
            "bilibili": {"source": "auto"},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(starter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}\nfill in the bucket and account id, then run --check")
        return 0

    config, config_file = load_config(args)
    cache_dir: pathlib.Path = config["_cache_dir"]

    r2_config = config.get("r2") or {}
    bili_config = dict(config.get("bilibili") or {})
    if args.bili_source:
        bili_config["source"] = args.bili_source
    backend_config = {
        "account_id": r2_config.get("account_id") or config.get("account_id"),
        "credentials_file": r2_config.get("credentials_file") or config.get("credentials_file"),
        "cred_reader": r2_config.get("cred_reader") or config.get("cred_reader"),
        "cred_target": r2_config.get("cred_target", "cf-evmedia-r2"),
        "repo_dirs": [str(pathlib.Path.cwd())] + [str(p) for p in pathlib.Path.cwd().parents][:4],
        "ffprobe": config.get("ffprobe"),
        **{k: v for k, v in bili_config.items() if k != "source"},
    }
    backend_config["source"] = bili_config.get("source", "auto")

    bucket = r2_config.get("bucket") or config.get("bucket")
    prefix = r2_config.get("prefix", DEFAULT_PREFIX)
    roots = [pathlib.Path(expand(r)) for r in (config.get("roots") or [])]
    suffixes = tuple(config.get("video_suffixes") or (".mp4", ".mkv", ".ts"))
    min_age = (args.min_age_minutes if args.min_age_minutes is not None
               else float(config.get("min_age_minutes", 60)))
    tolerance = (args.duration_tolerance if args.duration_tolerance is not None
                 else float(config.get("duration_tolerance", 3.0)))

    if not roots:
        raise SystemExit("no roots to scan: pass --root, or write `roots` into "
                         f"{default_config_path()} (see --init)")
    if not bucket:
        raise SystemExit("no bucket configured: set `r2.bucket` in the config")
    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        raise SystemExit(f"roots that do not exist: {', '.join(missing)}")

    # -- everything remote, before anything local is hashed ------------------
    print(f"config      : {config_file or '(none; flags and defaults only)'}")
    print(f"roots       : {len(roots)}")
    creds = bk.resolve_credentials(backend_config)
    print(f"credentials : {creds['source']}")
    r2 = bk.R2(creds["account_id"], creds["access_key_id"], creds["secret_access_key"], bucket)
    print(f"R2          : listing s3://{bucket}/{prefix} ...", flush=True)
    started = time.time()
    objects = r2.list_objects(prefix)
    if not objects:
        raise SystemExit(f"R2 returned no object under s3://{bucket}/{prefix}; refusing to run "
                         "(a wrong bucket or prefix would look like 'R2 has nothing')")
    by_title = collections.defaultdict(list)
    for key, size, etag in objects:
        by_title[bk.norm(key.rsplit("/", 1)[-1])].append((key, size, etag))
    print(f"            {len(objects)} objects, {len(by_title)} distinct names, "
          f"{time.time() - started:.1f}s")

    bili = bk.Bilibili(backend_config, cache_dir)
    print(f"Bilibili    : {bili.source}"
          + (f" ({bili.biliup})" if bili.source == "biliup" else "")
          + f", cookies {bili.cookie_source}, parts cache {bili.cache_file.name}")
    print(f"            reading every 稿件's parts ...", flush=True)
    started = time.time()
    parts = bili.parts_index(refresh=args.refresh_bili)
    total_parts = sum(len(v) for v in parts.values())
    print(f"            {total_parts} parts / {len(parts)} distinct titles "
          f"({bili.fetched_live} 稿件 read live, {bili.from_cache} from cache, "
          f"{time.time() - started:.1f}s)")
    if bili.failures:
        print(f"            {len(bili.failures)} 稿件 Bilibili would not answer for:")
        for bv, reason in bili.failures[:5]:
            print(f"              {bv}: {reason.splitlines()[0][:110]}")
    if args.check:
        print("\nconfig, credentials and both platforms answered. Nothing was scanned.")
        return 0
    if bili.failures and not args.allow_partial_bili:
        raise SystemExit(
            f"refusing to run: {len(bili.failures)} 稿件 could not be read, and one of them "
            "may be the only place a local video is held.\nRetry in a few minutes (the part "
            "lists already read are cached), or pass --allow-partial-bili to accept the gap.")

    # -- local artifacts -----------------------------------------------------
    hashes = Hashes(cache_dir / "content-hashes.json")
    ffprobe = bk.ffprobe_path(backend_config)
    print(f"ffprobe     : {ffprobe or 'not found (mp4 headers are read directly instead)'}\n")

    artifacts, vanished = [], 0
    markers = tuple(config.get("workspace_markers") or WORKSPACE_MARKERS) + REPORT_NAMES
    for root in roots:
        videos, workspaces = scan_root(root, suffixes, markers)
        print(f"scan        : {root}  ->  {len(videos)} video file(s), "
              f"{len(workspaces)} workspace dir(s)")
        for path in videos:
            try:
                info = path.stat()
            except OSError:
                vanished += 1            # gone since the walk; nothing left to protect
                continue
            artifacts.append({"kind": "video", "path": path, "root": root,
                              "size": info.st_size, "mtime_ns": info.st_mtime_ns,
                              "title": bk.norm(path.name),
                              "age": (time.time() - info.st_mtime_ns / 1e9) / 60,
                              "duration": None, "duration_source": "", "report": None})
        for path, report in workspaces:
            footprint = directory_footprint(path)
            if footprint is None:
                vanished += 1
                continue
            size, newest_ns = footprint
            if report:
                title = bk.norm(pathlib.Path(str(report["output"])).name)
                duration = float(report["output_seconds"])
                duration_source = f"{report['_report']} output_seconds"
            else:
                title, duration, duration_source = bk.norm(path.name), None, ""
            artifacts.append({"kind": "scratch", "path": path, "root": root, "size": size,
                              "mtime_ns": newest_ns, "title": title,
                              "age": (time.time() - newest_ns / 1e9) / 60,
                              "duration": duration, "duration_source": duration_source,
                              "report": report})
    if args.only:
        artifacts = [a for a in artifacts
                     if any(needle in str(a["path"]) for needle in args.only)]
    if args.limit:
        artifacts = artifacts[:args.limit]
    if not artifacts:
        raise SystemExit("nothing to inspect: no video file and no scratch directory matched "
                         "(check the roots, --only and the video_suffixes)")
    if vanished:
        print(f"            ({vanished} artifact(s) disappeared while scanning and were skipped)")

    # -- phase 1: what Bilibili holds ----------------------------------------
    print(f"\nBilibili    : checking {len(artifacts)} artifact(s) against "
          f"{len(parts)} part titles ...", flush=True)
    for item in artifacts:
        if item["kind"] == "video":
            item["duration"], item["duration_source"] = bk.probe_duration(item["path"], ffprobe)
        report = item["report"]
        if item["kind"] == "scratch" and report is None:
            item["blocked"] = "workspace with no export report (interrupted export)"
        elif item["kind"] == "scratch" and str(report.get("status")) != "complete":
            item["blocked"] = f"export status is {report.get('status')!r}, not complete"
        elif not item["duration"]:
            item["blocked"] = "no duration to compare"
        else:
            hit = bk.Bilibili.lookup(parts, item["title"], item["duration"], tolerance)
            if hit:
                item["bili"] = {"bvid": hit[0], "part_seconds": hit[1],
                                "read": bili.provenance.get(hit[0], "live")}
            else:
                item["blocked"] = "no B站 part with this title and duration"

    # -- phase 2: re-read, live, every 稿件 a decision rests on --------------
    # A cached part list is evidence of what Bilibili held when it was written. A part
    # edited or removed since would otherwise let a local file be deleted that Bilibili no
    # longer carries, so anything about to be deleted is judged on a reading from this run.
    stale = sorted({item["bili"]["bvid"] for item in artifacts
                    if item.get("bili") and item["bili"]["read"] == "cache"})
    if stale:
        print(f"Bilibili    : confirming {len(stale)} 稿件 live (their parts came from the "
              f"cache) ...", flush=True)
        started = time.time()
        live = bili.confirm(stale)
        print(f"            {sum(len(v) for v in live.values())} parts in "
              f"{time.time() - started:.1f}s")
        if bili.failures and not args.allow_partial_bili:
            raise SystemExit(
                f"refusing to delete: {len(bili.failures)} 稿件 could not be re-read, and one "
                f"of them may be the only place a local video is held. First: "
                f"{bili.failures[0][0]} - {bili.failures[0][1].splitlines()[0][:120]}")
        for item in artifacts:
            if not item.get("bili") or item["bili"]["read"] != "cache":
                continue
            hit = bk.Bilibili.lookup(live, item["title"], item["duration"], tolerance)
            if hit:
                item["bili"] = {"bvid": hit[0], "part_seconds": hit[1], "read": "live"}
            else:
                item["bili"] = None
                item["blocked"] = "B站 no longer holds this part (the cached list was stale)"

    # -- phase 3: R2, and only for what Bilibili already confirmed -----------
    counts = collections.Counter()
    bytes_by_decision = collections.Counter()
    candidates_for_r2 = sum(1 for item in artifacts if item.get("bili"))
    print(f"R2          : checking {candidates_for_r2} artifact(s) ...", flush=True)
    shown = 0
    for index, item in enumerate(artifacts, 1):
        detail = {"title": item["title"]}
        if item.get("bili"):
            detail["bili"] = item["bili"]
            candidates = by_title.get(item["title"], [])
            if not candidates:
                item["decision"], item["reason"] = "keep", "R2 has no object with this name"
            else:
                expected = item["size"] if item["kind"] == "video" else item["report"]["bytes"]
                if item["kind"] == "scratch":
                    match = [c for c in candidates if c[1] == expected]
                    ok = bool(match)
                    detail["r2"] = (f"{match[0][0]} ({bk.human(expected)})" if ok else
                                    f"no object of {bk.human(expected)}; bucket has "
                                    + ", ".join(f"{c[0]} ({bk.human(c[1])})" for c in candidates[:2]))
                else:
                    ok, why = r2_proof(item["path"], expected, candidates, hashes, args.size_only)
                    detail["r2"] = why
                if not ok:
                    item["decision"], item["reason"] = "keep", f"R2: {detail['r2']}"
                elif item["age"] < min_age:
                    item["decision"], item["reason"] = (
                        "keep", f"modified {item['age']:.0f} min ago (< {min_age:.0f}); an export "
                                "may still be running")
                else:
                    item["decision"], item["reason"] = "delete", "proven on both platforms"
        else:
            item["decision"] = "keep"
            item["reason"] = item.get("blocked") or "not verified"
        item["detail"] = detail
        counts[item["decision"] if item["decision"] == "delete" else item["reason"]] += 1
        bytes_by_decision[item["decision"]] += item["size"]
        if args.verbose:
            print(f"  [{index}/{len(artifacts)}] {item['decision']:<6} {item['path'].name[:70]}"
                  + ("" if item["decision"] == "delete" else f"  <- {item['reason'][:70]}"))
        elif item["decision"] == "delete" and shown < 40:
            shown += 1
            print(f"  delete  {item['path'].name[:74]}")
        elif index % 100 == 0:
            print(f"  ... {index}/{len(artifacts)}")
    if shown >= 40 and len([a for a in artifacts if a["decision"] == "delete"]) > shown:
        print(f"  delete  ... and {len([a for a in artifacts if a['decision'] == 'delete']) - shown} "
              "more (the report lists every one)")

    doomed = [a for a in artifacts if a["decision"] == "delete"]
    print("\n" + "-" * 78)
    print(f"{'DELETABLE':<12}{len(doomed):>5} artifact(s)  {bk.human(bytes_by_decision['delete'])}")
    for label, count in counts.most_common():
        if label != "delete":
            print(f"  left alone  {count:>5}  {label}")
    print(f"{'KEPT':<12}{len(artifacts) - len(doomed):>5} artifact(s)  "
          f"{bk.human(bytes_by_decision['keep'])}")
    by_root = collections.Counter()
    for item in doomed:
        by_root[str(item["root"])] += item["size"]
    for root, size in by_root.most_common():
        print(f"  {root}: {bk.human(size)}")

    report = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "applied" if args.apply else "dry-run",
        "config": str(config_file) if config_file else None,
        "bucket": f"s3://{bucket}/{prefix}",
        "objects": len(objects),
        "bilibili": {"source": bili.source, "cookie_source": bili.cookie_source,
                     "parts": total_parts, "titles": len(parts),
                     "fetched_live": bili.fetched_live, "from_cache": bili.from_cache,
                     "confirmed_live": len(stale), "failures": bili.failures},
        "totals": {"deletable": {"count": len(doomed), "bytes": bytes_by_decision["delete"]},
                   "kept": {"count": len(artifacts) - len(doomed),
                            "bytes": bytes_by_decision["keep"]}},
        "artifacts": [],
    }
    report_path = pathlib.Path(expand(args.report)) if args.report else cache_dir / "last-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def write_report() -> None:
        # Built at write time, not before the deletions: the report is the audit trail, so
        # an artifact's entry has to say what actually happened to it (deleted, failed,
        # changed since it was verified), not what was planned.
        report["artifacts"] = [{k: (str(v) if isinstance(v, pathlib.Path) else v)
                                for k, v in item.items() if k != "report"}
                               for item in artifacts]
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    write_report()
    hashes.save()

    if not args.apply:
        print(f"\ndry run: nothing deleted. Re-run with --apply to remove {len(doomed)} "
              f"artifact(s) ({bk.human(bytes_by_decision['delete'])}).")
        print(f"report -> {report_path}")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    freed, failed, changed, already = 0, [], [], 0
    for item in doomed:
        path = item["path"]
        # The proofs describe the bytes seen when they were taken. Hashing 68 GB takes
        # minutes, and an export can rewrite a file inside that window, so what is about to
        # be removed has to still be the thing that was verified.
        if not unchanged_since_verified(item):
            if not path.exists():
                already += 1
                item["decision"], item["reason"] = "gone", "already removed by another pass"
            else:
                changed.append(str(path))
                item["decision"], item["reason"] = (
                    "keep", "changed after it was verified (size or mtime moved)")
            continue
        try:
            if item["kind"] == "video":
                path.unlink()
            else:
                shutil.rmtree(path, onerror=lambda _f, p, _e: os.chmod(p, 0o700)
                              or shutil.rmtree(p))
            freed += item["size"]
            item["decision"], item["reason"] = "deleted", "proven on both platforms"
        except OSError as error:
            failed.append((str(path), str(error)))
            item["decision"], item["reason"] = "delete-failed", str(error)
    report["mode"] = "applied"
    report["deleted"] = {"count": len(doomed) - len(failed) - len(changed) - already,
                         "bytes": freed, "changed_since_verified": changed,
                         "already_gone": already, "failed": failed}
    write_report()
    print(f"\ndeleted {report['deleted']['count']} artifact(s), {bk.human(freed)} freed")
    if already:
        print(f"   {already} already removed by another pass")
    for path in changed:
        print(f"   ! {path}: changed after it was verified, left alone")
    for path, error in failed:
        print(f"   ! {path}: {error}")
    print(f"report -> {report_path}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
