"""Keeps the videos/3D prefixes in sync while the WebGIS export is still running.

Written in Python rather than PowerShell on purpose. Windows PowerShell 5.1 decodes a .ps1
without a BOM as ANSI, so the Chinese course names in the mapping below came out as mojibake
and the script would not even parse -- the same trap already written down in
tools/covers/README.md, walked into again an hour later. Python source is UTF-8 by default,
so the names are safe here.

Re-running the uploader is safe for finished courses but not free: the skip decision hashes
every local file whose size matches a remote object, so re-checking a complete course costs
gigabytes of disk reads for no work. This compares local counts against the catalog and only
revisits courses that are still short.

    python webgis-sync.py [--max-rounds N] [--interval SECONDS]
"""

import argparse
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
BASE = REPO / "verify_fresh" / "out" / "WebGIS课程"
PYTHON = r"D:\DevelopmentTools\Anaconda\python.exe"
UPLOADER = REPO / "tools" / "upload_to_r2.py"
CATALOG = REPO / "verify_fresh" / "catalog.json"
BUCKET = "cyrus-media-private"

sys.path.insert(0, str(REPO / "tools"))
import upload_to_r2  # noqa: E402  (needs the path above)

_S3 = None


def s3_client():
    """Built once, on first use, so importing this module has no side effects."""
    global _S3
    if _S3 is None:
        _S3 = upload_to_r2.client(upload_to_r2.credentials())
    return _S3

# Local course folder -> R2 prefix. The prefixes are the empty folder markers that already
# existed under videos/3D/, which line up one-to-one with the ten courses in the catalog.
COURSES = {
    "WebGIS地理概念": "videos/3D/webgis/basic/duyi-edu/",
    "OpenLayers框架详解": "videos/3D/openlayers/framework/duyi-edu/",
    "OpenLayers项目实战": "videos/3D/openlayers/basic/duyi-edu/",
    "Leaflet框架详解": "videos/3D/leaflet/framework/duyi-edu/",
    "Mapbox框架详解": "videos/3D/mapbox/framework/duyi-edu/",
    "高德 API与AntV L7": "videos/3D/gmap/api/duyi-edu/",
    "地理图形设计与数据服务": "videos/3D/geography/graphics/duyi-edu/",
    "Cesium基础入门": "videos/3D/cesium/basic/duyi-edu/",
    "Cesium高级进阶": "videos/3D/cesium/advanced/duyi-edu/",
    "Cesium项目实战": "videos/3D/cesium/course/duyi-edu/",
}


def catalog_counts() -> dict[str, int]:
    """Video count per course, read from the platform's own listing."""
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    counts = {}

    def walk(node):
        videos = [c for c in node.get("children", []) if c.get("kind") == "video"]
        if videos and node.get("title") in COURSES:
            counts[node["title"]] = len(videos)
        for child in node.get("children", []):
            if child.get("kind") != "video":
                walk(child)

    for root in value["roots"]:
        if "WebGIS" in root["title"]:
            walk(root)
    return counts


VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")


def local_videos(name: str) -> int:
    """Videos in the local folder, ignoring anything else.

    Counting every file made a course look finished two files early once a `封面/` folder
    with its two posters appeared inside it, so the covers are excluded by suffix.
    """
    folder = BASE / name
    if not folder.exists():
        return 0
    return sum(1 for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)


def remote_videos(prefix: str) -> int:
    """Objects under the prefix that carry bytes; the folder markers are zero-length."""
    found = upload_to_r2.list_remote(s3_client(), BUCKET, prefix)
    return sum(1 for size, _etag in found.values() if size > 0)


def upload(name: str, prefix: str) -> str:
    result = subprocess.run(
        [PYTHON, str(UPLOADER), "--local", str(BASE / name), "--prefix", prefix, "--apply"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in result.stdout.splitlines():
        if line.startswith(("uploaded ", "SKIP")):
            return " ".join(line.split())
    tail = result.stdout.strip().splitlines()
    return f"no uploads ({tail[-1].strip() if tail else 'no output'})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-rounds", type=int, default=14)
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()

    want = catalog_counts()
    missing = [c for c in COURSES if c not in want]
    if missing:
        print("catalog did not name:", missing, file=sys.stderr)
    for name in COURSES:
        (BASE / name).mkdir(parents=True, exist_ok=True)

    # The loop tracks upload debt -- local videos the bucket does not hold yet -- rather than
    # how much of the course has been downloaded. The first version stopped when every folder
    # matched the catalog, which said nothing about the bucket: two courses finished
    # downloading after their round had passed them by, and the watcher exited "complete"
    # with 126 videos never uploaded.
    for round_no in range(1, args.max_rounds + 1):
        short = [(n, local_videos(n), remote_videos(COURSES[n])) for n in COURSES]
        short = [(n, have, up) for n, have, up in short if up < have]
        stamp = time.strftime("%H:%M:%S")
        total_debt = sum(h - u for _n, h, u in short)

        if not short:
            print(f"[{stamp}] round {round_no}: bucket holds every downloaded video "
                  f"({sum(local_videos(n) for n in COURSES)} across {len(COURSES)} courses)")
            return 0

        print(f"[{stamp}] round {round_no}: {len(short)} course(s) behind, {total_debt} video(s) to go -> "
              + ", ".join(f"{n} {u}/{h}" for n, h, u in short), flush=True)
        for name, have, up in short:
            if have == 0:
                continue
            print(f"           {name:<24} {upload(name, COURSES[name])}", flush=True)

        if round_no < args.max_rounds:
            time.sleep(args.interval)

    print(f"watcher stopped after {args.max_rounds} rounds at {time.strftime('%H:%M:%S')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
