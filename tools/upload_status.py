"""Three-way comparison per collection: what is on disk, on Bilibili, and in R2.

Every episode is classified, not just counted. Totals alone cannot tell "both platforms hold
it" from "one platform holds everything and the other holds nothing", and the second is the
case that actually needs attention. So the platform's own catalog listing supplies the
universe of episodes and each episode is tested by title against the local files, the 稿件's
part list and the bucket's object names.

Everything is read live. A cached dump goes stale the moment a 稿件 is submitted or an export
finishes, and a stale status table is worse than none.

The collection-to-prefix and collection-to-稿件 tables come from clean_work.py, where they were
established by hand and then checked by uploading against them. They are not derived by pairing
on lesson numbers: every course numbers its lessons from 01, so the number sets are near
identical across collections and Jaccard pairs a WebGIS course with python/basic.

    python upload_status.py [--out <path>]

Default output is verify_fresh/上传状态.md, beside the data it describes.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
CATALOG = REPO / "verify_fresh" / "catalog.json"
DEFAULT_REPORT = REPO / "verify_fresh" / "上传状态.md"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")

sys.path.insert(0, str(REPO / "tools"))
import clean_work as cw          # noqa: E402  (verified prefix / 稿件 tables)
import upload_to_r2              # noqa: E402

ORDER = [
    "AI 大全栈/AI/Agents底层逻辑",
    "AI 大全栈/AI/LangGraph工作流开发",
    "AI 大全栈/AI/LangChain + DeepAgent 开发实战",
    "AI 大全栈/Python/Python语言核心精讲",
    "AI 大全栈/Python/Python框架",
    "AI 大全栈/Python/数据科学工具包",
    "AI 大全栈/通识/数据库",
    "AI 大全栈/通识/OAuth2",
    "AI 大全栈/通识/RBAC",
    "前端架构课程/LangChain",
    "前端架构课程/LangGraph",
    "前端架构课程/NestJS",
    "WebGIS课程/WebGIS地理概念",
    "WebGIS课程/OpenLayers框架详解",
    "WebGIS课程/OpenLayers项目实战",
    "WebGIS课程/Leaflet框架详解",
    "WebGIS课程/Mapbox框架详解",
    "WebGIS课程/高德 API与AntV L7",
    "WebGIS课程/地理图形设计与数据服务",
    "WebGIS课程/Cesium基础入门",
    "WebGIS课程/Cesium高级进阶",
    "WebGIS课程/Cesium项目实战",
    "前端架构课程/企业级监控平台全栈架构设计",
    "前端架构课程/企业级文档协同实践",
    "前端架构课程/音视频实时互动技术",
]

LABEL = {
    "AI 大全栈/AI/LangChain + DeepAgent 开发实战": "LangChain + DeepAgent",
    "AI 大全栈/Python/Python语言核心精讲": "Python语言核心精讲",
    "AI 大全栈/Python/Python框架": "Python框架",
    "AI 大全栈/Python/数据科学工具包": "数据科学工具包",
    "AI 大全栈/通识/数据库": "数据库",
    "AI 大全栈/通识/OAuth2": "OAuth2",
    "AI 大全栈/通识/RBAC": "RBAC",
    "前端架构课程/LangChain": "前端架构/LangChain",
    "前端架构课程/LangGraph": "前端架构/LangGraph",
    "前端架构课程/NestJS": "前端架构/NestJS",
    "前端架构课程/企业级监控平台全栈架构设计": "企业级监控平台全栈架构设计",
    "前端架构课程/企业级文档协同实践": "企业级文档协同实践",
    "前端架构课程/音视频实时互动技术": "音视频实时互动技术",
}


def norm(title: str) -> str:
    t = re.sub(r"\.(mp4|mkv|ts)$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


def catalog_episodes() -> dict:
    """collection title -> set of normalised episode titles, from the platform's listing.

    A collection may keep its lessons directly under itself or split them into chapter folders;
    both shapes are gathered, and the larger set wins when a title repeats down the tree the
    way WebGIS课程/WebGIS课程/WebGIS课程/... does.
    """
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    found = {}

    def walk(node):
        if node.get("title") and node.get("kind") != "video":
            deep = []

            def gather(inner):
                for child in inner.get("children", []):
                    if child.get("kind") == "video":
                        deep.append(child)
                    else:
                        gather(child)

            gather(node)
            if deep:
                titles = {norm(v["title"]) for v in deep}
                if len(titles) > len(found.get(node["title"], set())):
                    found[node["title"]] = titles
        for child in node.get("children", []):
            if child.get("kind") != "video":
                walk(child)

    for root in value["roots"]:
        walk(root)
    return found


def local_titles(folder: pathlib.Path) -> set:
    if not folder.is_dir():
        return set()
    return {norm(p.name) for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES}


def bili_titles(bvs) -> set:
    """Part titles for these 稿件, retried.

    A single failed fetch returned an empty set, which the table then rendered as "0 on
    Bilibili" -- indistinguishable from a collection that was never submitted. One run showed
    Cesium基础入门 at 0 while the 稿件 held 32 parts, and looking again by hand showed the
    fetch had simply failed. Retrying, and reporting a hard failure instead of zero, keeps that
    from being read as a fact about the upload.
    """
    titles = set()
    for bv in bvs:
        for attempt in range(3):
            result = subprocess.run([str(cw.BILIUP), "-u", str(cw.COOKIES), "show", bv],
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
            brace = result.stdout.find("{")
            if brace >= 0:
                try:
                    data = json.loads(result.stdout[brace:])
                except json.JSONDecodeError:
                    data = None
                if data is not None:
                    titles |= {norm(v.get("title", "")) for v in data.get("videos", [])}
                    break
            time.sleep(2 * (attempt + 1))
        else:
            raise SystemExit(f"could not read {bv} after 3 attempts; refusing to report it as 0")
    return titles


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    catalog = catalog_episodes()
    s3 = upload_to_r2.client(upload_to_r2.credentials())

    r2_cache = {}
    for prefix in sorted(set(cw.R2_BY_COLLECTION.values())):
        r2_cache[prefix] = {norm(k.rsplit("/", 1)[-1])
                            for k, (size, _e) in
                            upload_to_r2.list_remote(s3, "cyrus-media-private", prefix).items()
                            if size > 0 and k.lower().endswith(VIDEO_SUFFIXES)}

    bili_cache = {}
    for rel, bvs in cw.BILI_BY_COLLECTION.items():
        bili_cache[rel] = bili_titles(bvs)

    keys = ORDER + [k for k in cw.R2_BY_COLLECTION if k not in ORDER]
    rows, totals, skipped = [], [0] * 7, []
    for rel in keys:
        title = pathlib.PurePosixPath(rel).name
        universe = catalog.get(title)
        if not universe:
            skipped.append(rel)
            continue
        prefix = cw.R2_BY_COLLECTION.get(rel)
        r2 = r2_cache.get(prefix, set()) if prefix else set()
        bili = bili_cache.get(rel, set())
        local = local_titles(OUT / rel)

        in_r2, in_bili = universe & r2, universe & bili
        both = in_r2 & in_bili
        cells = [len(local), len(in_bili), len(in_r2), len(both),
                 len(in_r2 - in_bili), len(in_bili - in_r2),
                 len(universe - in_r2 - in_bili)]
        rows.append((LABEL.get(rel, title), cells))
        totals = [t + c for t, c in zip(totals, cells)]

    lines = [
        "# 三方比对结果（本地 / B站 / R2）",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "逐集判定，不是比总数。以平台清单里的集为全集，按**分集标题**分别检验本地文件、B站分P、R2 对象，",
        "所以「两边都有」的含义是**该集在两个平台都能找到**，而不是两边数量凑巧相等。",
        "「本地」是 `verify_fresh/out` 下现存的视频数 —— 已确认两边都有之后本地就删了，因此多数为 0 是正常的。",
        "",
        "| 合集 | 本地 | B站 | R2 | 两边都有 | 只R2 | 只B站 | 都无 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, cells in rows:
        lines.append(f"| {label} | " + " | ".join(str(c) for c in cells) + " |")
    lines.append("| **合计** | **" + "** | **".join(str(t) for t in totals) + "** |")
    lines.append("")
    if skipped:
        lines.append(f"未列入（清单里找不到对应集）：{', '.join(skipped)}")
        lines.append("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.out}  ({len(rows)} collections)")
    print("  合计: 本地=%d B站=%d R2=%d 两边=%d 只R2=%d 只B站=%d 都无=%d" % tuple(totals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
