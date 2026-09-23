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

    python bili_upload.py [--only <substring> ...] [--apply]

Without --apply it prints the plan and submits nothing.
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
]


def norm(title: str) -> str:
    t = re.sub(r"\.(mp4|mkv|ts)$", "", str(title).strip(), flags=re.I)
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


def archive_index(wanted: set) -> dict:
    """稿件 title -> {bv, set of part titles}, only for the collections we care about."""
    index = {}
    for line in run("list", "--max-pages", "60").splitlines():
        if not line.startswith("BV"):
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        bv, title = fields[0], fields[1].strip()
        if title not in wanted:
            continue
        raw = run("show", bv)
        brace = raw.find("{")
        if brace < 0:
            continue
        try:
            data = json.loads(raw[brace:])
        except json.JSONDecodeError:
            continue
        parts = {norm(v.get("title", "")) for v in data.get("videos", [])}
        if len(parts) >= len(index.get(title, {}).get("parts", set())):
            index[title] = {"bv": bv, "parts": parts}
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
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
            missing = [p for p in videos if norm(p.name) not in entry["parts"]]
            if not missing:
                print(f"skip  {title}: all {len(videos)} local video(s) are already parts of "
                      f"{entry['bv']}")
                continue
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
