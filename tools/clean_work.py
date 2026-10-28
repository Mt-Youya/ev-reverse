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
import subprocess
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
WORK = REPO / "verify_fresh" / "work"
OUT = REPO / "verify_fresh" / "out"
CATALOG = REPO / "verify_fresh" / "catalog.json"
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"

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
    "AI 大全栈/AI/LangChain + DeepAgent 开发实战": ["BV137hk6SE1T"],
    "前端架构课程/LangChain": ["BV1QZhk6HEr3"],
    "前端架构课程/LangGraph": ["BV13Rhk6sE1b"],
    "前端架构课程/NestJS": ["BV19Qhk6xEiM"],
    "前端架构课程/企业级监控平台全栈架构设计": ["BV1VVhk6pEet"],
    "前端架构课程/企业级文档协同实践": ["BV135hk6QEjG"],
    "前端架构课程/音视频实时互动技术": ["BV1o2hk6fEX6", "BV1FdhE6iExc"],
    # Submitted from a parallel session and never recorded here, which is why the 243 GB of
    # scratch behind them looked unverifiable. Read off `biliup list`, not inferred from titles.
    "前端架构课程/微前端架构实战": ["BV1Zph86oEMZ"],
    "前端架构课程/CSS企业应用方案": ["BV1wchW6UEDC"],
    "前端架构课程/微前端": ["BV1ZghW6rEjT"],
    "前端架构课程/工程管理实战": ["BV1K9hp6WEyX"],
    "前端架构课程/D3.js": ["BV1bwhn6nEoX"],
    "前端架构课程/前端测试框架": ["BV1hfhJ6pE7E"],
    "前端架构课程/前端工具链": ["BV1zqhb6bE1W"],
    # Created 2026-09-24 by the one-pass upload, so it did not exist when the table above was
    # first written and this collection's 10 episodes read as "not on Bilibili".
    "前端架构课程/新自定义脚手架": ["BV1P1aw6AEoB"],
    # No 稿件 exists yet for these eleven -- their files were only published from the merged
    # scratch on 2026-09-24. An empty list is a fact ("nothing holds these lessons"), where a
    # missing entry would read as "unknown" on the status page.
    "前端架构课程/Electron": ["BV1zdaw6sEbr"],
    "前端架构课程/React源码分析": ["BV1p5aP6wEBm"],
    "前端架构课程/文档协同": ["BV1A5aP6wEam"],
    "前端架构课程/脚手架技术内幕": ["BV1YXaA6jEC5"],
    "前端架构课程/运维自动化项目实战": ["BV13XaA6jE8n"],
    "前端架构课程/vite深度剖析": ["BV1w5aP6AE5S"],
    "前端架构课程/前端服务监控": ["BV1p8ac6WEJs"],
    "前端架构课程/rollup": ["BV1n8ac6WE6M"],
    "前端架构课程/国际化（i18n）": ["BV1jtaP6pEc5"],
    "前端架构课程/企业级包管理方案": ["BV1H5aP6wEBX"],
    "前端架构课程/esbuild": ["BV1H8ac6WEfW"],
    # The 25 never-exported courses have no 稿件 yet. An empty list is a fact -- "nothing holds
    # these lessons" -- and stays true until the first file is uploaded and sync_bili_ids fills the
    # id in. A missing entry would instead read as "unknown" on the status page.
    "鸿蒙Next课程/HarmonyOS Next SDK": [],
    "鸿蒙Next课程/ArkUI应用框架": [],
    "鸿蒙Next课程/HarmonyOS应用模型": [],
    "鸿蒙Next课程/HarmonyOS Next 特色与生态": [],
    "鸿蒙Next课程/坚果驾考": [],
    "鸿蒙Next课程/实战案例-简记": [],
    "鸿蒙Next课程/daysmatter": [],
    "鸿蒙Next课程/实战案例-华夏旅行者": [],
    "鸿蒙Next课程/鸿蒙就业指导": ["BV1zAaN6tE51"],
    "鸿蒙Next课程/鸿蒙认证考试辅导": ["BV1JAaN6bEHp"],
    "前端课程/求职之道极速版": [],
    "前端课程/求职之道常规版": [],
    "前端架构课程/前端+AI（基础篇）": [],
    "前端架构课程/React源码解析": ["BV1Prpb6EE9L"],
    "前端架构课程/vue源码解析(新)": ["BV1LJpb6aEKG"],
    "前端架构课程/vue源码分析": [],
    "前端架构课程/vue-router源码分析": ["BV1gVpn6eEri"],
    "前端架构课程/从零实现Coding Agent": [],
    "前端架构课程/AI平台篇（扣子&Dify）": [],
    "前端架构课程/通用组件库开发": ["BV1BopJ6LEij"],
    "前端架构课程/浏览器插件开发": ["BV1QTp86bEz9"],
    "前端架构课程/Recoil源码分析": ["BV1MEpn6cEBP"],
    "前端架构课程/pinia源码": ["BV1WhaN6uES5"],
    "前端架构课程/RxJS": ["BV1wdaP6PEZh"],
    "前端架构课程/主题切换": ["BV1xhaN6uE7p"],
    # Three AI courses the exporter produced on 2026-10-09 and 2026-10-10. Their 稿件 were created
    # through the creator centre (docs/BILIBILI-UPLOAD.md section 8) rather than by bili_upload, so
    # sync_bili_ids never saw a title it recognised and left these as empty lists -- which reads as
    # "nothing holds these lessons" and would have had a later pass create a second 稿件 for each.
    # Recorded here by hand from the BVs the upload actually produced.
    "AI 大全栈/AI/项目_LangGraph实现AI漫剧": ["BV1KApg6hEq7"],
    "AI 大全栈/AI/Agent观测与评估": ["BV1LCpu65EzC"],
    "AI 大全栈/AI/项目：Agent基础设施建设": ["BV1HLpG6cE1F"],
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
    "前端架构课程/NestJS": "videos/backend/nodejs/nestjs/duyi-edu/",
    "前端架构课程/企业级监控平台全栈架构设计": "videos/frontend/enterprise-monitoring/duyi-edu/",
    "前端架构课程/企业级文档协同实践": "videos/frontend/doc-collaboration/duyi-edu/",
    "前端架构课程/音视频实时互动技术": "videos/frontend/realtime-media/duyi-edu/",
    "前端架构课程/工程管理实战": "videos/frontend/engineering-management/duyi-edu/",
    "前端架构课程/D3.js": "videos/frontend/d3js/duyi-edu/",
    # Established 2026-09-24 by intersecting each local collection's filenames with the bucket's
    # object names, because prefix names alone are ambiguous: `frontend/scaffolding` really is
    # 新自定义脚手架 (7 of its 9 filenames were already there), while `frontend/react`,
    # `frontend/gsap` and `backend/nodejs/koa` hold files that are not in this account's
    # catalogue at all and must never be reused as a course's home.
    "前端架构课程/CSS企业应用方案": "videos/frontend/css-enterprise/duyi-edu/",
    "前端架构课程/前端工具链": "videos/frontend/toolchain/duyi-edu/",
    "前端架构课程/前端测试框架": "videos/frontend/testing/duyi-edu/",
    "前端架构课程/微前端": "videos/frontend/micro-frontend/duyi-edu/",
    "前端架构课程/微前端架构实战": "videos/frontend/micro-frontend/architecture-duyi-edu/",
    "前端架构课程/新自定义脚手架": "videos/frontend/scaffolding/duyi-edu/",
    # Eleven courses that had no home because nothing of theirs had ever been published. Their
    # scratch held merged lessons from days earlier (see export_missing.py --from-cache), so
    # publishing them is what created the files these prefixes now receive. Named by content, the
    # way the rest of the tree is: NestJS is a 前端架构课程 course and lives under backend/nodejs.
    # `react`, `gsap` and `koa` are deliberately not reused -- they hold files from another source,
    # not this account's catalogue.
    "前端架构课程/Electron": "videos/frontend/electron/duyi-edu/",
    "前端架构课程/React源码分析": "videos/frontend/react-internals/duyi-edu/",
    "前端架构课程/文档协同": "videos/frontend/doc-sync/duyi-edu/",
    "前端架构课程/脚手架技术内幕": "videos/frontend/scaffolding-internals/duyi-edu/",
    "前端架构课程/vite深度剖析": "videos/frontend/vite/duyi-edu/",
    "前端架构课程/rollup": "videos/frontend/rollup/duyi-edu/",
    "前端架构课程/esbuild": "videos/frontend/esbuild/duyi-edu/",
    "前端架构课程/前端服务监控": "videos/frontend/service-monitoring/duyi-edu/",
    "前端架构课程/国际化（i18n）": "videos/frontend/i18n/duyi-edu/",
    "前端架构课程/企业级包管理方案": "videos/frontend/package-management/duyi-edu/",
    "前端架构课程/运维自动化项目实战": "videos/backend/ops-automation/duyi-edu/",
    # The 25 courses that were never downloaded at all: no local file, no prefix, no 稿件. Their
    # prefixes are what make them plannable -- export_missing refuses to offer a lesson it cannot
    # call missing, and with no mapping the platform answer is unknown rather than false.
    # Named per docs/NEVER-EXPORTED-COURSES.md (agreed 2026-09-24), by content and reusing the
    # tree's existing top-level categories except for two new ones: `harmonyos` (the 鸿蒙 group)
    # and `career` (求职之道). The two pairs that are easy to confuse stay distinct on purpose:
    # React源码分析 is react-internals while React源码解析 is react-source, and vue源码解析(新) is
    # vue-source while vue源码分析 is vue-internals.
    "鸿蒙Next课程/HarmonyOS Next SDK": "videos/harmonyos/next-sdk/duyi-edu/",
    "鸿蒙Next课程/ArkUI应用框架": "videos/harmonyos/arkui/duyi-edu/",
    "鸿蒙Next课程/HarmonyOS应用模型": "videos/harmonyos/app-model/duyi-edu/",
    "鸿蒙Next课程/HarmonyOS Next 特色与生态": "videos/harmonyos/ecosystem/duyi-edu/",
    "鸿蒙Next课程/坚果驾考": "videos/harmonyos/driving-test/duyi-edu/",
    "鸿蒙Next课程/实战案例-简记": "videos/harmonyos/case-notes/duyi-edu/",
    "鸿蒙Next课程/daysmatter": "videos/harmonyos/daysmatter/duyi-edu/",
    "鸿蒙Next课程/实战案例-华夏旅行者": "videos/harmonyos/case-traveler/duyi-edu/",
    "鸿蒙Next课程/鸿蒙就业指导": "videos/harmonyos/career/duyi-edu/",
    "鸿蒙Next课程/鸿蒙认证考试辅导": "videos/harmonyos/certification/duyi-edu/",
    "前端课程/求职之道极速版": "videos/career/job-hunt-fast/duyi-edu/",
    "前端课程/求职之道常规版": "videos/career/job-hunt-standard/duyi-edu/",
    "前端架构课程/前端+AI（基础篇）": "videos/frontend/ai-basics/duyi-edu/",
    "前端架构课程/React源码解析": "videos/frontend/react-source/duyi-edu/",
    "前端架构课程/vue源码解析(新)": "videos/frontend/vue-source/duyi-edu/",
    "前端架构课程/vue源码分析": "videos/frontend/vue-internals/duyi-edu/",
    "前端架构课程/vue-router源码分析": "videos/frontend/vue-router-source/duyi-edu/",
    "前端架构课程/从零实现Coding Agent": "videos/frontend/coding-agent/duyi-edu/",
    "前端架构课程/AI平台篇（扣子&Dify）": "videos/ai/platform-coze-dify/duyi-edu/",
    "前端架构课程/通用组件库开发": "videos/frontend/component-library/duyi-edu/",
    "前端架构课程/浏览器插件开发": "videos/frontend/browser-extension/duyi-edu/",
    "前端架构课程/Recoil源码分析": "videos/frontend/recoil-source/duyi-edu/",
    "前端架构课程/pinia源码": "videos/frontend/pinia-source/duyi-edu/",
    "前端架构课程/RxJS": "videos/frontend/rxjs/duyi-edu/",
    # The one course that does not sit directly under `frontend`. 主题切换 is a CSS-technique
    # course -- the Bilibili tag it is submitted with is `CSS变量,设计令牌,暗色模式,主题,工程化` --
    # so it belongs in a `css/` group beside css-enterprise rather than at the frontend root.
    # Decided 2026-09-24, before its first upload: nothing was ever written to the old
    # `videos/frontend/theme-switching/` key, so this is where it lands rather than a move.
    "前端架构课程/主题切换": "videos/frontend/css/theme-switching/duyi-edu/",
    # Three AI courses the exporter produced on 2026-10-09, none of which had a home because
    # nothing of theirs had ever been published. Like the never-exported group, there is no
    # filename intersection to prove them against, so the names follow the tree's convention
    # (lowercase, hyphenated, by content) and stay clear of the existing `videos/ai/` keys:
    # `agents`, `langgraph`, `langchain`, `langchain/architecture-duyi-edu`,
    # `langgraph/architecture-duyi-edu` and `platform-coze-dify`. None collides.
    "AI 大全栈/AI/Agent观测与评估": "videos/ai/agent-observability/duyi-edu/",
    "AI 大全栈/AI/项目_LangGraph实现AI漫剧": "videos/ai/langgraph-comic/duyi-edu/",
    "AI 大全栈/AI/项目：Agent基础设施建设": "videos/ai/agent-infrastructure/duyi-edu/",
}


def norm(title: str) -> str:
    """Lesson name for comparison: no media extension, no whitespace.

    The extension pattern repeats on purpose, matching bili_upload.norm() and courses_json.norm().
    工程管理实战's lesson 35 is stored by the platform as `35. [测试体系]单元测试.mp4.mp4` while its
    R2 object and 稿件 part are named `35. [测试体系]单元测试.mp4`; stripping one extension left
    `35.[测试体系]单元测试.mp4` against `35.[测试体系]单元测试`, so the episode never looked verified,
    its 1.3 GB of merged scratch was never freed, and the publish backlog kept re-publishing it.
    """
    t = re.sub(r"(\.(mp4|mkv|ts))+$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


def path_key(name: str) -> str:
    """A collection name as these tables and the disk spell it.

    The tables' keys double as folder names under `verify_fresh/out`, so a platform name Windows
    refuses to put in a path is written with the offending character replaced: the course
    `项目:LangGraph实现AI漫剧` is `项目_LangGraph实现AI漫剧` in both tables, on disk, and in its
    稿件's own title. A lookup with the raw course name therefore matches nothing -- the status
    page reported that collection as never uploaded on either platform while R2 and Bilibili each
    held all nine lessons -- so every name-to-table lookup goes through this.

    A caller may hand it a node that names itself nowhere (None); that stays a miss, as it was
    when the lookup was a plain `in`, rather than becoming a TypeError.
    """
    return re.sub(r'[<>:"/\\|?*]', "_", str(name))


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



def fetch_bili_parts() -> dict:
    """bv -> {part title: duration}, fetched live.

    This used to read a cached dump, which went stale the moment new 稿件 were submitted and
    then reported every episode of those collections as "not on Bilibili" -- the cache simply
    had no entry for their BV. A 稿件 list is one API call per 稿件, so reading it fresh each
    run is cheap next to the mistake it prevents.
    """
    bvs = sorted({bv for bvs in BILI_BY_COLLECTION.values() for bv in bvs})
    out = {}
    for bv in bvs:
        result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "show", bv],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        brace = result.stdout.find("{")
        if brace < 0:
            out[bv] = {}
            continue
        try:
            data = json.loads(result.stdout[brace:])
        except json.JSONDecodeError:
            out[bv] = {}
            continue
        out[bv] = {norm(v.get("title", "")): v.get("duration") for v in data.get("videos", [])}
    return out


def local_collections():
    """The collections a Bilibili 稿件 exists for, as tuples of path parts.

    Taken from the 稿件 table rather than by scanning verify_fresh/out: the videos in the
    collections that were already cleaned up are gone from disk, so a scan that looks for
    video files stops recognising exactly the folders this script is meant to clear.
    """
    return [tuple(k.split("/")) for k in BILI_BY_COLLECTION]


def local_collection(video_path, collection_paths):
    """The local collection a catalog video belongs to.

    The catalog repeats a collection's title at every nesting level and then appends the chapter,
    so a lesson's path is `前端架构课程 / LangGraph / LangGraph / 第二章 快速入门 / <episode>`,
    while the folder on disk is only `前端架构课程/LangGraph`. Collapsing the repeated titles
    gives ["前端架构课程", "LangGraph", "第二章 快速入门"] -- the collection is no longer at the
    end, so looking only at suffixes found nothing and every chaptered course was silently
    reported as "not on Bilibili". Every contiguous run is tried instead, longest first, so the
    most specific name wins.

    Matching the catalog path as a prefix alone is not enough either: an earlier version took the
    deepest existing prefix, which resolved every WebGIS episode to the bare `WebGIS课程`
    (its path is `WebGIS课程 / WebGIS课程 / WebGIS课程 / WebGIS地理概念 / <episode>`) and
    reported all 220 of them as not uploaded.
    """
    collapsed = []
    for title in video_path[:-1]:
        if not collapsed or collapsed[-1] != title:
            collapsed.append(title)
    for length in range(len(collapsed), 0, -1):
        for start in range(len(collapsed) - length + 1):
            run = tuple(collapsed[start:start + length])
            if run in collection_paths:
                return "/".join(run)
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
    bili = fetch_bili_parts()
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

    # --- R2: object basename -> every full key with that name ---
    # Keyed by basename rather than by parent prefix: a collection's objects are not always flat
    # under its prefix (the LangGraph course nests them one level deeper, under 第一章/第二章/...),
    # and an exact parent-prefix lookup reported every one of those as missing from R2.
    r2_keys_by_name = collections.defaultdict(list)
    for key, size in keys:
        if size <= 0 or not key.lower().endswith((".mp4", ".mkv", ".ts")):
            continue
        r2_keys_by_name[norm(key.rsplit("/", 1)[-1])].append(key)

    def on_r2(prefix, title):
        return bool(prefix) and any(k.startswith(prefix) for k in r2_keys_by_name.get(title, ()))

    # --- Bilibili: collection -> {title: duration} ---
    bili_parts = {}
    for coll, bvs in BILI_BY_COLLECTION.items():
        parts = {}
        for bv in bvs:
            # fetch_bili_parts() already returns {part title: duration} per 稿件
            parts.update(bili.get(bv, {}))
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
        r2_ok = on_r2(prefix, title)

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

    sizes = {}

    def size_of(d):
        if d not in sizes:
            sizes[d] = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        return sizes[d]

    size = sum(size_of(d) for d in deletable)
    print(f"scratch dirs total          : {sum(1 for d in WORK.iterdir() if d.is_dir())}")
    print(f"deletable (on both, idle)   : {len(deletable)}  ({size / 1024**3:.2f} GB)")
    print(f"skipped, touched recently   : {len(skipped_recent)}")
    print(f"left alone, no R2 pair/hit  : {len(no_pair)}")
    print(f"left alone, not on B站       : {len(no_bili)}")
    if failed:
        print(f"left alone, unresolved      : {len(failed)}")

    def table(title, dirs):
        if not dirs:
            return
        per = collections.defaultdict(lambda: [0, 0])
        for d in dirs:
            cid, _, fid = d.name.partition("-")
            info = videos.get((cid, fid), {})
            coll = local_collection(info.get("path", []), collection_paths) or "(no collection)"
            per[coll][0] += 1
            per[coll][1] += size_of(d)
        print()
        print(f"{title} -- {len(dirs)} dirs, "
              f"{sum(size_of(d) for d in dirs) / 1024**3:.2f} GB")
        print(f"{'collection':<38}{'dirs':>6}{'GB':>8}")
        for coll, (n, by) in sorted(per.items(), key=lambda kv: -kv[1][1]):
            print(f'{coll[:36]:<38}{n:>6}{by / 1024**3:>8.2f}')

    table("deletable (on both platforms, idle)", deletable)
    table("left alone: R2 has no matching object", no_pair)
    table("left alone: no Bilibili 稿件 for this collection", no_bili)
    table("left alone: directory not resolvable", [d for d, _ in failed])

    if not args.apply:
        print(f"\ndry run: nothing deleted. Re-run with --apply to remove {len(deletable)} dir(s).")
        return 0

    freed = 0
    for d in deletable:
        freed += size_of(d)
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
