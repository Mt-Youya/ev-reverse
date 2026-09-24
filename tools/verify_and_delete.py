"""Verify that local course videos exist on both Bilibili and R2, then optionally delete them.

A title match is not proof. Each file has to pass two independent checks:

  * R2       -- the object exists AND its bytes match, by the rule the uploader itself uses to
                decide to skip: size, then MD5 against the ETag for a single PUT, or the MD5 of
                the part digests against `-N` for a multipart upload.
  * Bilibili -- the 稿件 that carries this collection has a part with the same title whose
                duration is within a few seconds of the local file's. Bilibili reports part
                durations, so a title match alone cannot pass off a different video with the
                same name.

Bilibili parts are fetched live; a cached list goes stale the moment a 稿件 is appended to,
and the append case is exactly the one that matters here.

Covers and the folder layout are never touched. Nothing remote is ever deleted -- the two
platforms holding the video is the entire reason the local copy can go.

    python verify_and_delete.py [--apply] [--report <path>]
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
BUCKET = "cyrus-media-private"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")
DURATION_TOLERANCE = 3.0

sys.path.insert(0, str(REPO / "tools"))
import upload_to_r2  # noqa: E402
import clean_work as cw  # noqa: E402

# local collection -> (R2 prefix, 稿件 ids), taken from the verified tables rather than repeated
# here. The copy that used to live in this file had already drifted: it was missing 前端工具链,
# 前端测试框架, CSS企业应用方案, 微前端, 微前端架构实战 and 新自定义脚手架, so those collections
# were never eligible for cleanup even after both platforms held them.
COLLECTIONS = {rel: (cw.R2_BY_COLLECTION[rel], cw.BILI_BY_COLLECTION.get(rel, []))
               for rel in cw.R2_BY_COLLECTION}


def norm(title: str) -> str:
    t = re.sub(r"\.(mp4|mkv|ts)$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


def bili_parts(bv: str) -> dict:
    """part title -> duration, read live so an append is never missed."""
    result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "show", bv],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    brace = result.stdout.find("{")
    if brace < 0:
        return {}
    data = json.loads(result.stdout[brace:])
    return {norm(v.get("title", "")): v.get("duration") for v in data.get("videos", [])}


def duration_of(path: pathlib.Path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except Exception:                                   # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=pathlib.Path,
                        default=pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp\deleted-videos.txt"))
    args = parser.parse_args()

    s3 = upload_to_r2.client(upload_to_r2.credentials())
    removable, blocked = [], []
    print(f"{'collection':<40}{'local':>6}{'B站':>5}{'R2':>5}{'both':>6}")
    print("-" * 62)

    for name, (prefix, bvs) in COLLECTIONS.items():
        folder = OUT / name
        if not folder.exists():
            continue
        parts = {}
        for bv in bvs:
            parts.update(bili_parts(bv))
        remote = {k.rsplit("/", 1)[-1]: v
                  for k, v in upload_to_r2.list_remote(s3, BUCKET, prefix).items() if v[0] > 0}

        local = [p for p in sorted(folder.rglob("*"))
                 if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]
        n_both = 0
        for path in local:
            key = norm(path.name)
            entry = remote.get(path.name)
            r2_ok = bool(entry) and upload_to_r2.already_there(path, entry[0], entry[1])
            if key in parts and parts[key] is not None:
                d = duration_of(path)
                bili_ok = d is not None and abs(d - float(parts[key])) <= DURATION_TOLERANCE
            else:
                bili_ok = False
            if r2_ok and bili_ok:
                n_both += 1
                removable.append((name, path))
            else:
                blocked.append((name, path, "R2" if not r2_ok else "B站"))
        print(f"{name[:38]:<40}{len(local):>6}{len(parts):>5}{len(remote):>5}{n_both:>6}")

    size = sum(p.stat().st_size for _n, p in removable)
    print("-" * 62)
    print(f"\nverified on BOTH platforms : {len(removable):>4}  ({size / 1024**3:.2f} GB)")
    print(f"left alone                 : {len(blocked):>4}")
    for name, path, why in blocked[:12]:
        print(f"    [{why}] {name} :: {path.name}")

    if not args.apply:
        print("\ndry run: nothing deleted (pass --apply)")
        return 0

    freed = 0
    gone = 0
    for _name, path in removable:
        # A pass of the watchdog can be deleting the same file at this moment: it runs this same
        # step, and both compute their list before either deletes. A file that is already gone is
        # not a failure -- the goal was for it not to be here.
        try:
            freed += path.stat().st_size
        except OSError:
            gone += 1
            continue
        path.unlink(missing_ok=True)
    args.report.write_text("\n".join(f"{n}\t{p}" for n, p in removable), encoding="utf-8")
    print(f"\ndeleted {len(removable) - gone} local file(s), {freed / 1024 ** 3:.2f} GB freed"
          + (f" ({gone} already removed by another pass)" if gone else ""))
    print(f"list -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
