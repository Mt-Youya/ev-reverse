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

# local collection -> (R2 prefix, 稿件 ids). The prefix table is hand-established and was
# verified by uploading against it; pairing prefixes to collections by lesson number does not
# work here, because every course numbers its lessons from 01 and the numbers are near
# identical across collections.
COLLECTIONS = {
    "AI 大全栈/AI/Agents底层逻辑": ("videos/ai/agents/duyi-edu/", ["BV1vTeS6nEQs"]),
    "AI 大全栈/AI/LangGraph工作流开发": ("videos/ai/langgraph/duyi-edu/", ["BV1Y3eS6mE7N"]),
    "AI 大全栈/AI/LangChain + DeepAgent 开发实战": ("videos/ai/langchain/duyi-edu/", ["BV137hk6SE1T"]),
    "AI 大全栈/Python/Python语言核心精讲": ("videos/python/basic/duyi-edu/", ["BV1mLe36hE3G"]),
    "AI 大全栈/Python/Python框架": ("videos/python/framework/duyi-edu/", ["BV1Cpe36dEyK"]),
    "AI 大全栈/Python/数据科学工具包": ("videos/python/math-tools/duyi-edu/", ["BV19beU6jExR"]),
    "AI 大全栈/通识/数据库": ("videos/database/postgresql/duyi-edu/", ["BV1mTe86UErP"]),
    "AI 大全栈/通识/OAuth2": ("videos/auth/oauth2/duyi-edu/", ["BV1mTe86UEck"]),
    "AI 大全栈/通识/RBAC": ("videos/auth/RBAC/duyi-edu/", ["BV1TTe86SEZ2"]),
    "前端架构课程/LangChain": ("videos/ai/langchain/architecture-duyi-edu/", ["BV1QZhk6HEr3"]),
    "前端架构课程/LangGraph": ("videos/ai/langgraph/architecture-duyi-edu/", ["BV13Rhk6sE1b"]),
    "前端架构课程/NestJS": ("videos/backend/nodejs/nestjs/duyi-edu/", ["BV19Qhk6xEiM"]),
    "前端架构课程/企业级监控平台全栈架构设计":
        ("videos/frontend/enterprise-monitoring/duyi-edu/", ["BV1VVhk6pEet"]),
    "前端架构课程/企业级文档协同实践":
        ("videos/frontend/doc-collaboration/duyi-edu/", ["BV135hk6QEjG"]),
    "前端架构课程/音视频实时互动技术":
        ("videos/frontend/realtime-media/duyi-edu/", ["BV1o2hk6fEX6"]),
    "WebGIS课程/WebGIS地理概念": ("videos/3D/webgis/basic/duyi-edu/", ["BV1hthq6qET3"]),
    "WebGIS课程/OpenLayers框架详解": ("videos/3D/openlayers/framework/duyi-edu/", ["BV1vnhq6GEHa"]),
    "WebGIS课程/OpenLayers项目实战": ("videos/3D/openlayers/basic/duyi-edu/", ["BV1eWhq6gEJc"]),
    "WebGIS课程/Leaflet框架详解": ("videos/3D/leaflet/framework/duyi-edu/", ["BV1s6hq6bEPy"]),
    "WebGIS课程/Mapbox框架详解": ("videos/3D/mapbox/framework/duyi-edu/", ["BV1Kqhq6aEHt"]),
    "WebGIS课程/高德 API与AntV L7": ("videos/3D/gmap/api/duyi-edu/", ["BV1xzhq6FECQ"]),
    "WebGIS课程/地理图形设计与数据服务":
        ("videos/3D/geography/graphics/duyi-edu/", ["BV1Lfhq6jEHf"]),
    "WebGIS课程/Cesium基础入门": ("videos/3D/cesium/basic/duyi-edu/", ["BV14ihq6wEUx"]),
    "WebGIS课程/Cesium高级进阶": ("videos/3D/cesium/advanced/duyi-edu/", ["BV1THh66uEzq"]),
    "WebGIS课程/Cesium项目实战": ("videos/3D/cesium/course/duyi-edu/", ["BV18Gh66LERW"]),
}


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
    for _name, path in removable:
        freed += path.stat().st_size
        path.unlink()
    args.report.write_text("\n".join(f"{n}\t{p}" for n, p in removable), encoding="utf-8")
    print(f"\ndeleted {len(removable)} local file(s), {freed / 1024**3:.2f} GB freed")
    print(f"list -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
