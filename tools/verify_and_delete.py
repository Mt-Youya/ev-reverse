"""Delete local course videos that are provably on both Bilibili and R2.

A title match alone is not proof. Two checks have to pass before a file is removed:

  * R2       -- the object exists AND its bytes match, by the same rule the uploader uses to
                decide to skip: size, then MD5 against the ETag for a single PUT, or md5 of
                the part digests against `-N` for a multipart upload.
  * Bilibili -- the 稿件 that carries this collection has a part with the same title AND a
                duration within a couple of seconds of the local file. Bilibili reports part
                durations, and a title alone would happily match a different video that
                happens to share a name.

Either copy being intact is enough to get the video back, which is the whole premise for
deleting it. Anything that fails a check is reported and left alone. Covers and the folder
layout are never touched.

    python verify_and_delete.py [--apply] [--report <path>]

Without --apply it prints exactly what it would remove and removes nothing.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
BILI_CACHE = pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp\bili-parts.json")
FFPROBE = "ffprobe"

sys.path.insert(0, str(REPO / "tools"))
import upload_to_r2  # noqa: E402

BUCKET = "cyrus-media-private"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")
DURATION_TOLERANCE = 3.0

# collection -> (R2 prefix, 稿件 ids that carry it)
COLLECTIONS = {
    "AI 大全栈/AI/Agents底层逻辑": ("videos/ai/agents/duyi-edu/", ["BV1vTeS6nEQs"]),
    "AI 大全栈/AI/LangGraph工作流开发": ("videos/ai/langgraph/duyi-edu/", ["BV1Y3eS6mE7N"]),
    "AI 大全栈/AI/LangChain + DeepAgent 开发实战": ("videos/ai/langchain/duyi-edu/", []),
    "AI 大全栈/Python/Python语言核心精讲": ("videos/python/basic/duyi-edu/", ["BV1mLe36hE3G"]),
    "AI 大全栈/Python/Python框架": ("videos/python/framework/duyi-edu/", ["BV1Cpe36dEyK"]),
    "AI 大全栈/Python/数据科学工具包": ("videos/python/math-tools/duyi-edu/", ["BV19beU6jExR"]),
    "AI 大全栈/通识/数据库": ("videos/database/postgresql/duyi-edu/", ["BV1mTe86UErP"]),
    "AI 大全栈/通识/OAuth2": ("videos/auth/oauth2/duyi-edu/", ["BV1mTe86UEck"]),
    "AI 大全栈/通识/RBAC": ("videos/auth/RBAC/duyi-edu/", ["BV1TTe86SEZ2"]),
    "前端架构课程/LangChain": ("videos/ai/langchain/architecture-duyi-edu/", []),
    "前端架构课程/LangGraph": ("videos/ai/langgraph/architecture-duyi-edu/", ["BV15FhB61E8X", "BV1LFhB61EyL"]),
    "前端架构课程/NestJS": ("", []),
    "WebGIS课程/WebGIS地理概念": ("videos/3D/webgis/basic/duyi-edu/", ["BV1hthq6qET3"]),
    "WebGIS课程/OpenLayers框架详解": ("videos/3D/openlayers/framework/duyi-edu/", ["BV1vnhq6GEHa"]),
    "WebGIS课程/OpenLayers项目实战": ("videos/3D/openlayers/basic/duyi-edu/", ["BV1eWhq6gEJc"]),
    "WebGIS课程/Leaflet框架详解": ("videos/3D/leaflet/framework/duyi-edu/", ["BV1s6hq6bEPy"]),
    "WebGIS课程/Mapbox框架详解": ("videos/3D/mapbox/framework/duyi-edu/", ["BV1Kqhq6aEHt"]),
    "WebGIS课程/高德 API与AntV L7": ("videos/3D/gmap/api/duyi-edu/", ["BV1xzhq6FECQ"]),
    "WebGIS课程/地理图形设计与数据服务": ("videos/3D/geography/graphics/duyi-edu/", ["BV1Lfhq6jEHf"]),
    "WebGIS课程/Cesium基础入门": ("videos/3D/cesium/basic/duyi-edu/", ["BV14ihq6wEUx"]),
    "WebGIS课程/Cesium高级进阶": ("videos/3D/cesium/advanced/duyi-edu/", ["BV1THh66uEzq"]),
    "WebGIS课程/Cesium项目实战": ("videos/3D/cesium/course/duyi-edu/", ["BV18Gh66LERW"]),
}


def norm(title: str) -> str:
    t = re.sub(r"\.(mp4|mkv|ts)$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


def duration_of(path: pathlib.Path) -> float | None:
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
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

    bili = json.loads(BILI_CACHE.read_text(encoding="utf-8"))
    s3 = upload_to_r2.client(upload_to_r2.credentials())

    deletable, blocked_r2, blocked_bili, no_bili_upload = [], [], [], []
    for name, (prefix, bvs) in COLLECTIONS.items():
        folder = OUT / name
        if not folder.exists():
            continue

        parts = {}
        for bv in bvs:
            for part in bili.get(bv, {}).get("parts", []):
                if part.get("title"):
                    parts[norm(part["title"])] = part.get("duration")

        remote = {}
        if prefix:
            remote = {k.rsplit("/", 1)[-1]: v
                      for k, v in upload_to_r2.list_remote(s3, BUCKET, prefix).items() if v[0] > 0}

        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            key = norm(path.name)

            entry = remote.get(path.name)
            r2_ok = bool(entry) and upload_to_r2.already_there(path, entry[0], entry[1])

            part_duration = parts.get(key)
            if not bvs:
                bili_ok = False
            elif part_duration is None:
                bili_ok = False
            else:
                local_duration = duration_of(path)
                bili_ok = (local_duration is not None
                           and abs(local_duration - float(part_duration)) <= DURATION_TOLERANCE)

            if r2_ok and bili_ok:
                deletable.append((name, path))
            elif not bvs:
                no_bili_upload.append((name, path))
            elif not r2_ok:
                blocked_r2.append((name, path))
            else:
                blocked_bili.append((name, path))

    total_bytes = sum(p.stat().st_size for _n, p in deletable)
    print(f"verified on BOTH platforms : {len(deletable):>4}  ({total_bytes / 1024**3:.2f} GB)")
    print(f"left alone, R2 check failed: {len(blocked_r2):>4}")
    print(f"left alone, B站 check failed: {len(blocked_bili):>4}")
    print(f"left alone, not on B站 at all: {len(no_bili_upload):>4}")

    if blocked_r2 or blocked_bili:
        print("\n-- examples left alone --")
        for label, group in (("R2", blocked_r2), ("B站", blocked_bili)):
            for name, path in group[:5]:
                print(f"   [{label}] {name} :: {path.name}")

    if not args.apply:
        print(f"\ndry run: nothing deleted. Re-run with --apply to remove these {len(deletable)} file(s).")
        return 0

    removed_bytes = 0
    for name, path in deletable:
        size = path.stat().st_size
        path.unlink()
        removed_bytes += size
    args.report.write_text("\n".join(f"{n}\t{p}" for n, p in deletable), encoding="utf-8")
    print(f"\ndeleted {len(deletable)} file(s), {removed_bytes / 1024**3:.2f} GB freed")
    print(f"list written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
