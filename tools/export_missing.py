"""Download the lessons that are missing locally and still absent from a platform.

The uploaders can only send what is on disk, so an episode whose local copy was deleted
after an earlier upload -- or never exported at all -- stays missing from R2 forever while
its neighbours are complete. The course list has the file id and the chapter it belongs to,
which is everything `evmedia.exe export-evs` needs, so the gap can be closed without
touching the GUI.

Only lessons that are genuinely absent are planned: a lesson that is on disk, or that both
platforms already hold, is skipped. The output path reproduces the exporter's own layout
(`out/<collection>/<chapter>/<lesson>.mp4`) so the file lands beside its siblings and the
existing uploader picks it up with no special case. A repeated media extension in the
platform's title is collapsed, because that artefact is what made a 1.1 GB file look
missing in the first place.

    python export_missing.py [--only <substring> ...] [--limit N] [--apply]

Without --apply it prints the plan and downloads nothing.
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
OUT = REPO / "verify_fresh" / "out"
WORK = REPO / "verify_fresh" / "work"
INDEX = REPO / "verify_fresh" / "index.json"
COURSES = REPO / "verify_fresh" / "upload-status" / "courses.json"
SESSION = REPO / "verify_fresh" / "session.json"
EVMEDIA = REPO / "target" / "release" / "evmedia.exe"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")

sys.path.insert(0, str(REPO / "tools"))
import clean_work as cw  # noqa: E402

# Lessons that cannot be published, so the planner stops offering them every pass. Each one needs
# a human decision, not another attempt:
#   (280626, 81102) 前端工具链 / 01. ESLint介绍 -- one truncated AAC frame fails the strict audio
#                   validation and the exporter refuses to publish. Reproducible across two
#                   independent downloads. See docs/EXPORT-PIPELINE.md §5.
UNPUBLISHABLE = {("280626", "81102")}


def safe_name(title: str) -> str:
    """The lesson's file name: one media extension, none of the repeats the platform stored."""
    stem = re.sub(r"(\.(mp4|mkv|ts))+$", "", str(title).strip(), flags=re.I)
    return stem + ".mp4"


def every_lesson() -> list:
    """Every lesson of every course, with the path it would be published to.

    The folder is the collection's own `rel` when the tables have one. Eleven courses have no
    mapping at all -- Electron, React源码分析, 文档协同, rollup and the rest -- and their lessons
    are still placed the way the exporter places them, `<category>/<course>/<chapter>/`, which is
    the layout those folders already have on disk.
    """
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    account = index.get("account_id")
    categories = {}
    catalog = json.loads((REPO / "verify_fresh" / "catalog.json").read_text(encoding="utf-8"))

    def label(node, root):
        if node.get("title"):
            categories.setdefault(node["title"], root)
        for child in node.get("children", []):
            if child.get("kind") != "video":
                label(child, root)

    for root in catalog["roots"]:
        label(root, root["title"])

    rows = []
    for cid, record in index["courses"].items():
        detail = record["course_detail"]
        name = detail["self"]["name"]
        collection = next((rel for rel in cw.R2_BY_COLLECTION
                           if pathlib.PurePosixPath(rel).name == name), None)
        if collection is None:
            category = categories.get(name)
            if category is None:
                continue
            collection = f"{category}/{name}"

        def walk(node, chapters):
            own = (node.get("self") or {}).get("name") or node.get("title")
            here = chapters + [own] if own and node is not detail else chapters
            for f in node.get("files", []):
                if f.get("type") != "video":
                    continue
                target = OUT / collection
                for chapter in here:
                    target = target / chapter
                target = target / safe_name(f.get("title", ""))
                rows.append({
                    "course": name, "course_id": cid, "file_id": f.get("file_id"),
                    "title": str(f.get("title", "")).removesuffix(".mp4"),
                    "output": target, "work": WORK / f"{cid}-{f.get('file_id')}",
                    "account": account,
                })
            for child in node.get("childs", []):
                walk(child, here)

        walk(detail, [])
    return rows


def plan(rows: list) -> list:
    """The lessons missing from a platform that are not on disk either.

    This is the set a download can fill. `cached()` is a subset of it in practice -- a lesson with
    a merged lesson.ts has no local file either -- and the two plans overlap, so a caller that runs
    both should publish first and let this shrink. The unpublishable lesson is excluded here too: a
    fresh download of it reproduces the same one-frame AAC defect, so planning it every pass would
    be two guaranteed minutes of nothing.
    """
    status = json.loads(COURSES.read_text(encoding="utf-8"))
    courses = {c["name"]: c for c in status["courses"]}

    gaps = []
    for row in rows:
        course = courses.get(row["course"])
        if course is None:
            continue
        if (str(row["course_id"]), str(row["file_id"])) in UNPUBLISHABLE:
            continue
        episode = next((e for e in course["episodes"] if e["file_id"] == row["file_id"]), None)
        if episode is None or episode["local"]:
            continue
        if episode["r2"] is not False and episode["bilibili"] is not False:
            continue
        if row["output"].exists():
            continue
        gaps.append(row)
    return gaps


def cached(rows: list) -> list:
    """The lessons that are already merged in work/ and have no published file.

    `--phase publish` validates and remuxes the cached `lesson.ts`, so this costs a decode per
    lesson instead of a download. Eleven courses were left in exactly this state: merged days
    ago, never published, and therefore never uploadable.
    """
    return [row for row in rows
            if not row["output"].exists() and (row["work"] / "lesson.ts").exists()
            and (str(row["course_id"]), str(row["file_id"])) not in UNPUBLISHABLE]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None,
                        help="substrings of course names to restrict to")
    parser.add_argument("--from-cache", action="store_true",
                        help="publish the lessons whose scratch already holds a merged lesson.ts, "
                             "instead of downloading the ones a platform is missing")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--smallest-first", action="store_true",
                        help="publish the collections with the fewest lessons first. Finishing a "
                             "small collection means it can be uploaded and its files deleted, "
                             "which is what keeps the disk from filling while a large one is still "
                             "being produced.")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    gaps = cached(every_lesson()) if args.from_cache else plan(every_lesson())
    if args.only:
        gaps = [g for g in gaps if any(key in g["course"] for key in args.only)]
    if args.smallest_first:
        per_course = collections.Counter(g["course"] for g in gaps)
        gaps.sort(key=lambda g: (per_course[g["course"]], g["course"]))
    if args.limit:
        gaps = gaps[:args.limit]

    # Printed in the order the plan will run, not sorted for looks: with --smallest-first the order
    # is the point, and a plan that lists the biggest collection first while doing the smallest one
    # first is a plan nobody can check.
    by_course = {}
    for gap in gaps:
        by_course.setdefault(gap["course"], []).append(gap)
    for course, rows in by_course.items():
        print(f"{course:<32} {len(rows):>3} lesson(s)")
        for row in rows:
            print(f"      file {row['file_id']:<9} {row['title'][:52]}")

    verb = "publish from cache" if args.from_cache else "download"
    print(f"\n{len(gaps)} lesson(s) to {verb}")
    if not args.apply:
        print(f"dry run: nothing will be {verb}ed (pass --apply)")
        return 0

    failures = []
    for number, gap in enumerate(gaps, 1):
        gap["output"].parent.mkdir(parents=True, exist_ok=True)
        command = [str(EVMEDIA), "export-evs",
                   "--session", str(SESSION), "--account", str(gap["account"]),
                   "--course", str(gap["course_id"]), "--file", str(gap["file_id"]),
                   "--output", str(gap["output"]), "--work", str(gap["work"]),
                   "--jobs", str(args.jobs)]
        if args.from_cache:
            command += ["--phase", "publish"]
        print(f"\n=== [{number}/{len(gaps)}] {gap['course']} :: {gap['title']} ===", flush=True)
        started = time.time()
        result = subprocess.run(command, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        elapsed = time.time() - started
        if result.returncode != 0 or not gap["output"].exists():
            failures.append(gap)
            print(f"    FAILED rc={result.returncode} after {elapsed:.0f}s: "
                  f"{(result.stderr or result.stdout or '')[-400:]}", flush=True)
        else:
            size = gap["output"].stat().st_size / 1024 ** 2
            print(f"    ok {size:.0f} MB in {elapsed:.0f}s", flush=True)

    print(f"\n{verb}ed {len(gaps) - len(failures)}/{len(gaps)}")
    for gap in failures:
        print(f"   failed: {gap['course']} :: {gap['title']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
