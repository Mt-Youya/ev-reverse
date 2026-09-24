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


def safe_name(title: str) -> str:
    """The lesson's file name: one media extension, none of the repeats the platform stored."""
    stem = re.sub(r"(\.(mp4|mkv|ts))+$", "", str(title).strip(), flags=re.I)
    return stem + ".mp4"


def plan() -> list:
    """[(course name, course id, file id, output path, work dir)] for every real gap."""
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    account = index.get("account_id")
    status = json.loads(COURSES.read_text(encoding="utf-8"))
    missing = {c["name"]: c for c in status["courses"]
               if c["counts"]["r2"] == 0 or c["counts"]["bilibili"] == 0
               or any(not e["local"] and (e["r2"] is False or e["bilibili"] is False)
                      for e in c["episodes"])}

    rows = []
    for cid, record in index["courses"].items():
        detail = record["course_detail"]
        name = detail["self"]["name"]
        if name not in missing:
            continue
        collection = None
        for rel in cw.R2_BY_COLLECTION:
            if pathlib.PurePosixPath(rel).name == name:
                collection = rel
                break
        if collection is None:
            continue

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

    # Only the ones with nothing on disk AND nothing on one of the platforms.
    gaps = []
    for row in rows:
        course = missing[row["course"]]
        episode = next((e for e in course["episodes"] if e["file_id"] == row["file_id"]), None)
        if episode is None or episode["local"]:
            continue
        if episode["r2"] is not False and episode["bilibili"] is not False:
            continue
        if row["output"].exists():
            continue
        gaps.append(row)
    return gaps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None,
                        help="substrings of course names to restrict to")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    gaps = plan()
    if args.only:
        gaps = [g for g in gaps if any(key in g["course"] for key in args.only)]
    if args.limit:
        gaps = gaps[:args.limit]

    by_course = {}
    for gap in gaps:
        by_course.setdefault(gap["course"], []).append(gap)
    for course, rows in sorted(by_course.items(), key=lambda r: -len(r[1])):
        print(f"{course:<32} {len(rows):>3} lesson(s)")
        for row in rows:
            print(f"      file {row['file_id']:<9} {row['title'][:52]}")

    print(f"\n{len(gaps)} lesson(s) to download into {OUT}")
    if not args.apply:
        print("dry run: nothing downloaded (pass --apply)")
        return 0

    failures = []
    for number, gap in enumerate(gaps, 1):
        gap["output"].parent.mkdir(parents=True, exist_ok=True)
        command = [str(EVMEDIA), "export-evs",
                   "--session", str(SESSION), "--account", str(gap["account"]),
                   "--course", str(gap["course_id"]), "--file", str(gap["file_id"]),
                   "--output", str(gap["output"]), "--work", str(gap["work"]),
                   "--jobs", str(args.jobs)]
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

    print(f"\ndownloaded {len(gaps) - len(failures)}/{len(gaps)}")
    for gap in failures:
        print(f"   failed: {gap['course']} :: {gap['title']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
