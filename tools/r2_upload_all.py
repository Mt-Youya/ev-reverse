"""Upload every locally exported collection to its R2 prefix, one collection per invocation.

The prefix table is the point of this file. Five of the twelve collections have no prefix yet, and
prefix names are ambiguous enough that two of them (`scaffolding`, `react`) look like they could
belong to a different course -- `scaffolding` really is 新自定义脚手架 (7 of 9 filenames already
there), while `react`, `gsap` and `koa` hold files that are not in this account's catalogue at all
and must not be reused.

Keys are `prefix + path relative to the collection folder`, so a chaptered course keeps its chapter
subfolders, matching how LangGraph is already stored under
`videos/ai/langgraph/architecture-duyi-edu/第二章 快速入门/`.

    python r2_upload_all.py [--apply] [--only <substring>]
"""

import argparse
import pathlib
import subprocess
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
UPLOADER = REPO / "tools" / "upload_to_r2.py"
PYTHON = sys.executable

# local collection -> R2 prefix. `new` marks a folder this run has to create.
PLAN = [
    ("AI 大全栈/AI/Agents底层逻辑", "videos/ai/agents/duyi-edu/", False),
    ("AI 大全栈/AI/LangChain + DeepAgent 开发实战", "videos/ai/langchain/duyi-edu/", False),
    ("前端架构课程/CSS企业应用方案", "videos/frontend/css-enterprise/duyi-edu/", True),
    ("前端架构课程/LangChain", "videos/ai/langchain/architecture-duyi-edu/", False),
    ("前端架构课程/LangGraph", "videos/ai/langgraph/architecture-duyi-edu/", False),
    ("前端架构课程/企业级监控平台全栈架构设计",
     "videos/frontend/enterprise-monitoring/duyi-edu/", False),
    ("前端架构课程/前端工具链", "videos/frontend/toolchain/duyi-edu/", True),
    ("前端架构课程/前端测试框架", "videos/frontend/testing/duyi-edu/", True),
    ("前端架构课程/工程管理实战", "videos/frontend/engineering-management/duyi-edu/", False),
    ("前端架构课程/微前端", "videos/frontend/micro-frontend/duyi-edu/", True),
    ("前端架构课程/微前端架构实战",
     "videos/frontend/micro-frontend/architecture-duyi-edu/", True),
    ("前端架构课程/新自定义脚手架", "videos/frontend/scaffolding/duyi-edu/", False),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    failures = []
    for collection, prefix, is_new in PLAN:
        if args.only and args.only not in collection:
            continue
        folder = OUT / collection
        if not folder.is_dir():
            print(f"skip  {collection}: not on disk")
            continue
        videos = [p for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".ts")]
        if not videos:
            print(f"skip  {collection}: no videos")
            continue

        command = [PYTHON, str(UPLOADER), "--local", str(folder), "--prefix", prefix,
                   "--include-images"]
        if args.apply:
            command.append("--apply")
        print(f"\n{'=' * 80}")
        print(f"{collection}  ({len(videos)} videos) -> {prefix}"
              f"{'   [NEW FOLDER]' if is_new else ''}")
        print("=" * 80, flush=True)
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        for line in (result.stdout or "").splitlines():
            if (line.startswith(("local:", "target:", "mode:", "bucket already", "SKIP", "UPLOAD",
                                 "uploaded", "verified", "nothing", "  !", "   !"))
                    or "FAILED" in line):
                print("  " + line)
        if result.returncode != 0:
            failures.append((collection, result.returncode, (result.stderr or "")[-300:]))
            print(f"  !! rc={result.returncode} {result.stderr[-200:]}")

    print()
    if failures:
        print(f"{len(failures)} collection(s) failed:")
        for collection, code, err in failures:
            print(f"  {collection}: rc={code} {err}")
        return 1
    print("all collections processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
