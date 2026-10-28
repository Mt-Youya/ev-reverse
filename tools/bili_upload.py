"""Submit each exported course to Bilibili as one multi-part 稿件.

One 稿件 per collection, titled with the collection name -- that is how the existing course
uploads are shaped, and Bilibili's own model for this is a multi-part submission rather than
one video per episode. `biliup upload` takes every path in one call and creates the parts.

A collection that already has a 稿件 is not simply skipped. The account's 稿件 for
LangGraph工作流开发 holds 23 parts while 11 of that course's episodes were never submitted,
and a rule of "skip when the 稿件 is at least as large as the folder" reads that as complete
-- the folder is smaller precisely because the rest was already uploaded and deleted. So the
missing episodes are worked out by part title and added with `biliup append`, which is what
that subcommand is for.

Videos are found recursively: the front-end LangGraph course keeps them in chapter folders,
and looking only at the collection's own level reports it as empty.

`tid` 208 and the cover-by-file-path shape were read back from the account's existing uploads
with `biliup show`, not guessed.

See `docs/BILIBILI-UPLOAD.md` for the constraints that fail silently when violated -- the
watermark defaulting to on, part order being the order they are sent, and why the creator
centre's batch page is not a substitute for this script.

    python bili_upload.py [--only <substring> ...] [--apply]

Without --apply it prints the plan and submits nothing.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bili_listing  # noqa: E402  (one cached read of the account's 稿件 list)
import clean_work as cw  # noqa: E402  (the recorded 稿件 ids per collection)

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")
TID = 208

# Bilibili adds a "bilibili + nickname" watermark to a submission unless the request says
# otherwise, and its web form defaults that checkbox to on. biliup has no watermark flag and
# does not send the field at all -- `watermark` is not part of its Studio struct, it only
# shows up in a test proving that arbitrary keys pass through `extra_fields`. The result was
# every 稿件 carrying a watermark nobody asked for.
#
# So the field is sent explicitly. `state` 0 is the intended meaning of "do not add it", but
# that is NOT confirmed yet: the test 稿件 re-uploaded with this flag went into review, and
# Bilibili will not serve a 稿件 under review for download, so its frames cannot be checked.
# The creator-centre endpoints that would expose the stored setting return 404.
#
# Confirm by downloading the test 稿件 (BV1FdhE6iExc) once the review passes: a frame with no
# "bilibili + nickname" mark in the top-left corner is the proof. Until then this is a
# parameter sent, not a watermark removed.
WATERMARK_OFF = json.dumps({"watermark": {"state": 0}}, separators=(",", ":"))

# local collection -> tags. The 稿件 title is the collection's folder name.
COLLECTIONS = [
    ("前端架构课程/LangChain", "LangChain,大模型,RAG,Agent,LLM"),
    ("前端架构课程/NestJS", "NestJS,Node.js,后端,框架,TypeScript"),
    ("前端架构课程/LangGraph", "LangGraph,Agent,工作流,LLM,大模型"),
    ("AI 大全栈/AI/LangGraph工作流开发", "LangGraph,Agent,工作流,LLM,大模型"),
    ("前端架构课程/企业级文档协同实践", "文档协同,知识库,架构,前端,项目实战"),
    ("前端架构课程/企业级监控平台全栈架构设计", "前端监控,SDK,Kafka,ClickHouse,架构"),
    ("AI 大全栈/AI/LangChain + DeepAgent 开发实战", "LangChain,DeepAgent,Agent,大模型,LLM"),
    ("前端架构课程/音视频实时互动技术", "音视频,WebRTC,实时通信,前端,架构"),
    ("前端架构课程/前端工具链", "前端,工程化,ESLint,Prettier,Babel"),
    # Added when the whole local export was pushed in one pass. Several of these already have a
    # 稿件 from an earlier manual upload; archive_index finds it by title and appends only the
    # episodes it lacks, instead of creating a second 稿件 for the same collection.
    ("AI 大全栈/AI/Agents底层逻辑", "Agent,大模型,LLM,AI,智能体"),
    ("前端架构课程/CSS企业应用方案", "CSS,Sass,Tailwind,postcss,前端工程化"),
    ("前端架构课程/前端测试框架", "Jest,单元测试,前端,测试,自动化测试"),
    ("前端架构课程/工程管理实战", "monorepo,工程化,包管理,CICD,前端"),
    ("前端架构课程/微前端", "微前端,micro-app,qiankun,前端,架构"),
    ("前端架构课程/微前端架构实战", "微前端,qiankun,无界,模块联邦,架构"),
    ("前端架构课程/新自定义脚手架", "脚手架,Node.js,CLI,工程化,npm"),
    # Published from the merged scratch on 2026-09-24 (export_missing.py --from-cache). None of
    # these had a 稿件, so archive_index finds nothing and each one becomes a new submission.
    ("前端架构课程/Electron", "Electron,桌面应用,IPC,跨平台,前端"),
    ("前端架构课程/React源码分析", "React,源码,Fiber,Hooks,调度"),
    ("前端架构课程/文档协同", "文档协同,协同编辑,架构,项目实战,前端"),
    ("前端架构课程/脚手架技术内幕", "脚手架,CLI,模板引擎,工程化,Node.js"),
    ("前端架构课程/运维自动化项目实战", "运维,自动化,部署,Docker,CI"),
    ("前端架构课程/vite深度剖析", "Vite,ESM,HMR,构建,前端工程化"),
    ("前端架构课程/前端服务监控", "前端监控,埋点,性能,SDK,上报"),
    ("前端架构课程/rollup", "rollup,打包,Tree-shaking,插件,工程化"),
    ("前端架构课程/国际化（i18n）", "国际化,i18n,前端,格式化,多语言"),
    ("前端架构课程/企业级包管理方案", "npm,monorepo,包管理,发布,工程化"),
    ("前端架构课程/esbuild", "esbuild,构建,Go,插件,性能"),
    # The 25 courses that were never exported (docs/NEVER-EXPORTED-COURSES.md, agreed 2026-09-24).
    # Tags match their cover cards, so poster and 稿件 say the same thing. None of them has a 稿件
    # yet, so each becomes a new submission the first time files appear -- which is why their covers
    # had to be delivered before this table was written: Bilibili fixes the cover and the watermark
    # at creation, and this pipeline cannot drive the creator centre to change either one.
    ("鸿蒙Next课程/HarmonyOS Next SDK", "ArkTS,Ability,UIAbility,SDK,鸿蒙"),
    ("鸿蒙Next课程/ArkUI应用框架", "ArkUI,声明式,状态管理,组件,鸿蒙"),
    ("鸿蒙Next课程/HarmonyOS应用模型", "Stage模型,Ability,生命周期,进程,鸿蒙"),
    ("鸿蒙Next课程/HarmonyOS Next 特色与生态", "元服务,分布式,多设备,生态,鸿蒙"),
    ("鸿蒙Next课程/坚果驾考", "ArkTS,项目实战,题库,页面路由,鸿蒙"),
    ("鸿蒙Next课程/实战案例-简记", "记事本,数据存储,ArkTS,实战,鸿蒙"),
    ("鸿蒙Next课程/daysmatter", "倒计时,组件,实战,ArkTS,鸿蒙"),
    ("鸿蒙Next课程/实战案例-华夏旅行者", "旅行,地图,实战,ArkTS,鸿蒙"),
    ("鸿蒙Next课程/鸿蒙就业指导", "就业,简历,面试,鸿蒙,职业规划"),
    ("鸿蒙Next课程/鸿蒙认证考试辅导", "认证,考试,考点,鸿蒙,备考"),
    ("前端课程/求职之道极速版", "求职,简历,面试,前端,职业规划"),
    ("前端课程/求职之道常规版", "求职,简历,面试,职业规划,前端"),
    ("前端架构课程/前端+AI（基础篇）", "LLM,Prompt,AI编码,提效,前端"),
    ("前端架构课程/React源码解析", "React,源码,Fiber,Reconciler,Hooks"),
    ("前端架构课程/vue源码解析(新)", "Vue3,响应式,编译器,渲染器,源码"),
    ("前端架构课程/vue源码分析", "Vue,响应式,虚拟DOM,Diff,源码"),
    ("前端架构课程/vue-router源码分析", "vue-router,路由,匹配器,导航守卫,源码"),
    ("前端架构课程/从零实现Coding Agent", "Agent,工具调用,上下文,LLM,实战"),
    ("前端架构课程/AI平台篇（扣子&Dify）", "扣子,Dify,工作流,编排,LLM"),
    ("前端架构课程/通用组件库开发", "组件库,设计规范,打包,文档站,工程化"),
    ("前端架构课程/浏览器插件开发", "Manifest,content script,背景页,消息通信,扩展"),
    ("前端架构课程/Recoil源码分析", "Recoil,atom,selector,状态,源码"),
    ("前端架构课程/pinia源码", "pinia,store,响应式,插件,Vue3"),
    ("前端架构课程/RxJS", "RxJS,Observable,操作符,调度器,响应式"),
    ("前端架构课程/主题切换", "CSS变量,设计令牌,暗色模式,主题,工程化"),
]


def norm(title: str) -> str:
    """Part title for comparison: no extension, no whitespace.

    The extension pattern repeats on purpose. A file named `35. [测试体系]单元测试.mp4.mp4` -- an
    export artefact -- reduced to `35.[测试体系]单元测试.mp4`, which never equals the 稿件's part
    title `35. [测试体系]单元测试`. The uploader therefore saw it as missing on every run and
    appended another copy; that part reached three copies before anyone noticed.
    """
    t = re.sub(r"(\.(mp4|mkv|ts))+$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def chapter_index(name: str) -> int:
    """Chapter number from a folder like `第三章 ESLint`, or 0 when there is none.

    Sorting the relative path as a string orders the Chinese chapter names by code point, which
    is 一 (U+4E00), 三 (U+4E09), 二 (U+4E8C), 五 (U+4E94), 四 (U+56DB) -- so a chaptered
    collection was submitted with its chapters in the order 一, 三, 二, 五, 四. Bilibili keeps
    parts in the order they arrive, so that order is what the 稿件 ended up with.
    """
    match = re.match(r"^第([一二三四五六七八九十]+)", name)
    return CN_DIGITS.get(match.group(1), 99) if match else 0


def course_order(folder: pathlib.Path, path: pathlib.Path):
    """(chapter numbers, path) so chapters sort as 一二三四五 and episodes by filename."""
    relative = path.relative_to(folder)
    chapters = [chapter_index(part) for part in relative.parts[:-1]]
    return (chapters, str(relative))


def run(*args) -> str:
    return subprocess.run([str(BILIUP), "-u", str(COOKIES), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


# 稿件s that must never receive another part. Bilibili carries two identical submissions for
# 音视频实时互动技术 -- BV1o2hk6fEX6 (watermarked, uploaded before the flag was understood) and
# BV1FdhE6iExc (clean, re-uploaded with `watermark.state = 0`). They hold the same three episodes,
# so `archive_index`'s "most parts wins" tie-break picks whichever `biliup list` happens to return
# last, and a new episode could land in the watermarked one. The watermark is fixed at 稿件
# creation, so a part appended there would carry it forever.
SUPERSEDED = {"BV1o2hk6fEX6"}

# Local episode -> the title its part actually carries in the 稿件, when the two differ.
#
# 前端工具链's first part was overwritten with the collection's own name: the batch upload page had
# that row selected when the collection title was typed, so 01. 课程概述 was submitted as
# 「前端工具链」 (see .scratch/bilibili-upload/issues/01). That mismatch held for weeks, and this
# table carried the workaround -- a file -> part-title alias, so the uploader would stop trying to
# append a lesson the 稿件 already held under another name.
#
# Both halves are resolved as of 2026-10-13, so the table is empty:
#   * the part is named correctly again (`01. 课程概述`, renamed through the creator centre), so
#     `01. 课程概述.mp4` matches a part title on its own;
#   * the lesson genuinely absent from that 稿件, `第三章 ESLint/01. ESLint介绍.mp4` (1863s), has
#     since been uploaded -- which for a while left two parts sharing the title `01. ESLint介绍`,
#     exactly what the rename separated.
# Kept as an empty dict rather than removed: main() reads it unconditionally, and an empty table is
# the claim "no collection needs an alias", which is a fact worth stating rather than implying.
PART_ALIASES = {}


def parts_of(bv: str) -> set:
    """The part titles a 稿件 holds, normalized; empty when it cannot be read."""
    raw = run("show", bv)
    brace = raw.find("{")
    if brace < 0:
        return set()
    try:
        data = json.loads(raw[brace:])
    except json.JSONDecodeError:
        return set()
    return {norm(v.get("title", "")) for v in data.get("videos", [])}


def archive_index(wanted: set) -> dict:
    """稿件 title -> {bv, set of part titles}, only for the collections we care about.

    The recorded id is used first, and the account-wide listing is only fetched for a collection
    that has none. That matters more than it looks: `biliup list --max-pages 60` walks ~49 pages of
    `member.bilibili.com/x/web/archives`, and Bilibili answers that endpoint with **HTTP 412** when
    it is called often -- three passes an hour plus a verification script was enough to trip it on
    2026-09-24, and a 412 used to stop Bilibili uploads entirely. `biliup show <bv>` is a different
    endpoint and keeps working, so a pass that knows its ids needs one request per collection it is
    actually uploading instead of fifty before it starts.

    The listing is still needed for a collection with no id yet -- that is how a 稿件 created
    outside this tool (a hand upload, or a parallel session) is found instead of duplicated.
    """
    index = {}
    unknown = []
    for rel, bvs in cw.BILI_BY_COLLECTION.items():
        title = pathlib.PurePosixPath(rel).name
        if title not in wanted:
            continue
        known = [bv for bv in bvs if bv not in SUPERSEDED]
        if known:
            index[title] = {"bv": known[0], "parts": parts_of(known[0])}
        else:
            unknown.append(title)

    if not unknown:
        return index

    listing = bili_listing.listing(max_age_minutes=0)
    for line in listing.splitlines():
        if not line.startswith("BV"):
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        bv, title = fields[0], fields[1].strip()
        if title not in unknown or bv in SUPERSEDED:
            continue
        parts = parts_of(bv)
        if len(parts) >= len(index.get(title, {}).get("parts", set())):
            index[title] = {"bv": bv, "parts": parts}
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--skip", nargs="*", default=None,
                        help="substrings of collections to leave alone. Needed when a collection's "
                             "only local file is one the 稿件 already holds under a different part "
                             "title -- appending it would create a duplicate part.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    wanted = {pathlib.Path(rel).name for rel, _tags in COLLECTIONS}
    index = archive_index(wanted)

    todo = []
    for rel, tags in COLLECTIONS:
        folder = OUT / rel
        if not folder.is_dir():
            continue
        if args.only and not any(key in rel for key in args.only):
            continue
        if args.skip and any(key in rel for key in args.skip):
            print(f"skip  {rel}: excluded via --skip")
            continue
        videos = sorted((p for p in folder.rglob("*")
                         if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES),
                        key=lambda p: course_order(folder, p))
        if not videos:
            print(f"skip  {rel}: no videos on disk")
            continue

        title = folder.name
        entry = index.get(title)
        cover = folder / "封面" / f"{title}-合集封面-16x9.png"

        if entry is None:
            todo.append(("upload", rel, title, tags, videos, cover, None))
        else:
            aliases = PART_ALIASES.get(title, {})
            missing = [p for p in videos
                       if norm(p.name) not in entry["parts"]
                       and norm(aliases.get(p.name, "")) not in entry["parts"]]
            if not missing:
                print(f"skip  {title}: all {len(videos)} local video(s) are already parts of "
                      f"{entry['bv']}")
                continue
            for path in videos:
                if norm(path.name) not in entry["parts"] and path not in missing:
                    print(f"      {path.name} is already a part of {entry['bv']}, "
                          f"under the title {aliases[path.name]!r}")
            todo.append(("append", rel, title, tags, missing, cover, entry["bv"]))

    total = sum(p.stat().st_size for _m, _r, _t, _g, vids, _c, _b in todo for p in vids)
    for mode, rel, title, tags, videos, cover, bv in todo:
        size = sum(p.stat().st_size for p in videos) / 1024**3
        target = f"append to {bv}" if mode == "append" else "new 稿件"
        print(f"{title:<34} {len(videos):>3} video(s) {size:>6.2f} GB  {target}  "
              f"cover={'yes' if cover.exists() else 'NO'}")

    if not args.apply:
        print(f"\n{len(todo)} submission(s), {total / 1024**3:.2f} GB -- dry run, nothing sent")
        return 0

    for mode, rel, title, tags, videos, cover, bv in todo:
        if mode == "append":
            command = [str(BILIUP), "-u", str(COOKIES), "append", "--vid", bv]
        else:
            command = [str(BILIUP), "-u", str(COOKIES), "upload",
                       "--title", title, "--tid", str(TID), "--tag", tags]
        if cover.exists():
            command += ["--cover", str(cover)]
        if mode == "append":
            command += ["--title", title, "--tid", str(TID), "--tag", tags]
        command += ["--extra-fields", WATERMARK_OFF]
        command += [str(p) for p in videos]

        print(f"\n=== {mode} {title} ({len(videos)} part(s)) ===", flush=True)
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        for line in result.stdout.splitlines():
            if any(k in line for k in ("投稿成功", "ResponseData", "Upload completed", "bvid")):
                print("   ", line.strip()[-160:])
        if result.returncode != 0:
            print(f"    FAILED rc={result.returncode}: {result.stderr[-400:]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
