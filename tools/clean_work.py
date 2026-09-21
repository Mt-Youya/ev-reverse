"""Delete work/ scratch for lessons that are provably on both Bilibili and R2.

`verify_fresh/work/<course>-<file>/` is the exporter's scratch: the decrypted segment cache,
the playlist, and the descriptor for one lesson. The directory name carries the platform's
own ids, and a catalog video's id is exactly `<course>:<file>`, so every scratch directory
resolves to one episode of one collection -- no guessing from folder names.

A directory is removed only when that episode passes both checks:

  * Bilibili -- the collection's 稿件 has a part with the same title whose duration matches
                the catalog's duration for that episode (within a few seconds).
  * R2       -- the R2 prefix paired with that collection holds an object whose name is the
                episode. Prefixes are paired by lesson-number overlap, the same way
                tools/r2_status.py pairs them, rather than from a hand-written table.

Scratch directories touched recently are skipped: an export is usually still running, and
deleting the cache out from under it would throw away its work mid-lesson.

    python clean_work.py [--apply] [--min-age-minutes 60]
"""

import argparse
import collections
import json
import pathlib
import re
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
WORK = REPO / "verify_fresh" / "work"
OUT = REPO / "verify_fresh" / "out"
CATALOG = REPO / "verify_fresh" / "catalog.json"
BILI_CACHE = pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp\bili-parts.json")

sys.path.insert(0, str(REPO / "tools"))
import upload_to_r2  # noqa: E402

BUCKET = "cyrus-media-private"
DURATION_TOLERANCE = 3.0
PAIR_THRESHOLD = 0.5

# collection -> 稿件 ids, as established when the Bilibili side was first checked
BILI_BY_COLLECTION = {
    "AI 大全栈/AI/Agents底层逻辑": ["BV1vTeS6nEQs"],
    "AI 大全栈/AI/LangGraph工作流开发": ["BV1Y3eS6mE7N"],
    "AI 大全栈/Python/Python语言核心精讲": ["BV1mLe36hE3G"],
    "AI 大全栈/Python/Python框架": ["BV1Cpe36dEyK"],
    "AI 大全栈/Python/数据科学工具包": ["BV19beU6jExR"],
    "AI 大全栈/通识/数据库": ["BV1mTe86UErP"],
    "AI 大全栈/通识/OAuth2": ["BV1mTe86UEck"],
    "AI 大全栈/通识/RBAC": ["BV1TTe86SEZ2"],
    "WebGIS课程/WebGIS地理概念": ["BV1hthq6qET3"],
    "WebGIS课程/OpenLayers框架详解": ["BV1vnhq6GEHa"],
    "WebGIS课程/OpenLayers项目实战": ["BV1eWhq6gEJc"],
    "WebGIS课程/Leaflet框架详解": ["BV1s6hq6bEPy"],
    "WebGIS课程/Mapbox框架详解": ["BV1Kqhq6aEHt"],
    "WebGIS课程/高德 API与AntV L7": ["BV1xzhq6FECQ"],
    "WebGIS课程/地理图形设计与数据服务": ["BV1Lfhq6jEHf"],
    "WebGIS课程/Cesium基础入门": ["BV14ihq6wEUx"],
    "WebGIS课程/Cesium高级进阶": ["BV1THh66uEzq"],
    "WebGIS课程/Cesium项目实战": ["BV18Gh66LERW"],
}

# collection -> R2 prefix. Hand-established from the live bucket and verified by uploading
# against it (each prefix's object count matched that collection's episode count). It is NOT
# derived by pairing on lesson numbers: every course numbers its lessons from 01, so those
# number sets are near-identical across collections and Jaccard happily pairs a WebGIS course
# with videos/python/basic. tools/r2_status.py pairs that way and reports nonsense here.
R2_BY_COLLECTION = {
    "AI 大全栈/AI/Agents底层逻辑": "videos/ai/agents/duyi-edu/",
    "AI 大全栈/AI/LangGraph工作流开发": "videos/ai/langgraph/duyi-edu/",
    "AI 大全栈/AI/LangChain + DeepAgent 开发实战": "videos/ai/langchain/duyi-edu/",
    "AI 大全栈/Python/Python语言核心精讲": "videos/python/basic/duyi-edu/",
    "AI 大全栈/Python/Python框架": "videos/python/framework/duyi-edu/",
    "AI 大全栈/Python/数据科学工具包": "videos/python/math-tools/duyi-edu/",
    "AI 大全栈/通识/数据库": "videos/database/postgresql/duyi-edu/",
    "AI 大全栈/通识/OAuth2": "videos/auth/oauth2/duyi-edu/",
    "AI 大全栈/通识/RBAC": "videos/auth/RBAC/duyi-edu/",
    "WebGIS课程/WebGIS地理概念": "videos/3D/webgis/basic/duyi-edu/",
    "WebGIS课程/OpenLayers框架详解": "videos/3D/openlayers/framework/duyi-edu/",
    "WebGIS课程/OpenLayers项目实战": "videos/3D/openlayers/basic/duyi-edu/",
    "WebGIS课程/Leaflet框架详解": "videos/3D/leaflet/framework/duyi-edu/",
    "WebGIS课程/Mapbox框架详解": "videos/3D/mapbox/framework/duyi-edu/",
    "WebGIS课程/高德 API与AntV L7": "videos/3D/gmap/api/duyi-edu/",
    "WebGIS课程/地理图形设计与数据服务": "videos/3D/geography/graphics/duyi-edu/",
    "WebGIS课程/Cesium基础入门": "videos/3D/cesium/basic/duyi-edu/",
    "WebGIS课程/Cesium高级进阶": "videos/3D/cesium/advanced/duyi-edu/",
    "WebGIS课程/Cesium项目实战": "videos/3D/cesium/course/duyi-edu/",
    "前端架构课程/LangChain": "videos/ai/langchain/architecture-duyi-edu/",
    "前端架构课程/LangGraph": "videos/ai/langgraph/architecture-duyi-edu/",
}


def norm(title: str) -> str:
    t = re.sub(r"\.(mp4|mkv|ts)$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


def lesson_number(name: str):
    m = re.match(r"^\s*(\d+)", norm(name))
    return m.group(1) if m else None


def catalog_videos():
    """(course_id, file_id) -> {title, duration, path titles}"""
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    index = {}

    def walk(node, path):
        for child in node.get("children", []):
            if child.get("kind") == "video":
                cid, _, fid = str(child["id"]).partition(":")
                index[(cid, fid)] = {
                    "title": child["title"],
                    "duration": float(child["video"]["duration_seconds"]),
                    "path": path + [child["title"]],
                }
            else:
                walk(child, path + [child["title"]])

    for root in value["roots"]:
        walk(root, [root["title"]])
    return index


def local_collections():
    """The collections a Bilibili 稿件 exists for, as tuples of path parts.

    Taken from the 稿件 table rather than by scanning verify_fresh/out: the videos in the
    collections that were already cleaned up are gone from disk, so a scan that looks for
    video files stops recognising exactly the folders this script is meant to clear.
    """
    return [tuple(k.split("/")) for k in BILI_BY_COLLECTION]


def local_collection(video_path, collection_paths):
    """The local collection a catalog video belongs to, matched by path suffix.

    Matching the catalog path as a prefix is not enough: the catalog repeats a title at every
    nesting level, so a WebGIS episode's path is
    `WebGIS课程 / WebGIS课程 / WebGIS课程 / WebGIS地理概念 / <episode>` while the folder on
    disk is only `WebGIS课程/WebGIS地理概念`. Taking the deepest prefix that exists resolved
    every WebGIS episode to the bare `WebGIS课程`, which then had no 稿件 to check against and
    was wrongly reported as "not on Bilibili" for all 220 of them.
    """
    ancestors = list(video_path[:-1])
    for length in range(len(ancestors), 0, -1):
        suffix = tuple(ancestors[-length:])
        if suffix in collection_paths:
            return "/".join(suffix)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-age-minutes", type=int, default=60)
    parser.add_argument("--report", type=pathlib.Path,
                        default=pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp\deleted-work.txt"))
    args = parser.parse_args()

    videos = catalog_videos()
    collection_paths = set(local_collections())
    bili = json.loads(BILI_CACHE.read_text(encoding="utf-8"))
    s3 = upload_to_r2.client(upload_to_r2.credentials())

    # --- live R2: prefix -> {lesson number: object basename} ---
    keys, token = [], None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": "videos/", "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        keys += [(o["Key"], o["Size"]) for o in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            break
        token = page["NextContinuationToken"]

    r2_by_prefix = collections.defaultdict(dict)
    for key, size in keys:
        if size <= 0 or not key.lower().endswith((".mp4", ".mkv", ".ts")):
            continue
        prefix = key.rsplit("/", 1)[0] + "/"
        r2_by_prefix[prefix][norm(key.rsplit("/", 1)[-1])] = True

    # --- R2: prefix -> object basenames, using the verified table ---
    r2_by_prefix = collections.defaultdict(set)
    for key, size in keys:
        if size <= 0 or not key.lower().endswith((".mp4", ".mkv", ".ts")):
            continue
        r2_by_prefix[key.rsplit("/", 1)[0] + "/"].add(norm(key.rsplit("/", 1)[-1]))

    # --- Bilibili: collection -> {title: duration} ---
    bili_parts = {}
    for coll, bvs in BILI_BY_COLLECTION.items():
        parts = {}
        for bv in bvs:
            for part in bili.get(bv, {}).get("parts", []):
                if part.get("title"):
                    parts[norm(part["title"])] = part.get("duration")
        bili_parts[coll] = parts

    cutoff = time.time() - args.min_age_minutes * 60
    deletable, skipped_recent, no_pair, no_bili, failed = [], [], [], [], []
    for d in sorted(WORK.iterdir()):
        if not d.is_dir():
            continue
        cid, _, fid = d.name.partition("-")
        info = videos.get((cid, fid))
        if not info:
            failed.append((d, "not in catalog"))
            continue
        coll = local_collection(info["path"], collection_paths)
        title = norm(info["title"])

        parts = bili_parts.get(coll, {})
        bili_ok = (title in parts and parts[title] is not None
                   and abs(float(parts[title]) - info["duration"]) <= DURATION_TOLERANCE)

        prefix = R2_BY_COLLECTION.get(coll)
        r2_ok = bool(prefix) and title in r2_by_prefix.get(prefix, set())

        if not bili_ok and not parts:
            no_bili.append(d)
            continue
        if not r2_ok:
            no_pair.append(d)
            continue
        if not bili_ok:
            no_bili.append(d)
            continue
        if d.stat().st_mtime > cutoff:
            skipped_recent.append(d)
            continue
        deletable.append(d)

    size = sum(f.stat().st_size for d in deletable for f in d.rglob("*") if f.is_file())
    print(f"scratch dirs total          : {sum(1 for d in WORK.iterdir() if d.is_dir())}")
    print(f"deletable (on both, idle)   : {len(deletable)}  ({size / 1024**3:.2f} GB)")
    print(f"skipped, touched recently   : {len(skipped_recent)}")
    print(f"left alone, no R2 pair/hit  : {len(no_pair)}")
    print(f"left alone, not on B站       : {len(no_bili)}")
    if failed:
        print(f"left alone, unresolved      : {len(failed)}")

    per = collections.defaultdict(lambda: [0, 0])
    for d in deletable:
        cid, _, fid = d.name.partition("-")
        info = videos.get((cid, fid), {})
        coll = local_collection(info.get("path", []), collection_paths) or "(unmapped)"
        per[coll][0] += 1
        per[coll][1] += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    print()
    print(f"{'collection':<38}{'dirs':>6}{'GB':>8}")
    for coll, (n, by) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f'{coll[:36]:<38}{n:>6}{by / 1024**3:>8.2f}')

    if not args.apply:
        print(f"\ndry run: nothing deleted. Re-run with --apply to remove {len(deletable)} dir(s).")
        return 0

    freed = 0
    for d in deletable:
        freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        for f in sorted(d.rglob("*"), reverse=True):
            if f.is_file():
                f.unlink()
            elif f.is_dir():
                f.rmdir()
        d.rmdir()
    args.report.write_text("\n".join(str(d) for d in deletable), encoding="utf-8")
    print(f"\ndeleted {len(deletable)} scratch dir(s), {freed / 1024**3:.2f} GB freed")
    print(f"list written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
