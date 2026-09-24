"""Report the health of every 稿件 the tables know: duplicate parts, and coverage.

The uploader appends by comparing part titles, so a title it cannot match is appended again on
the next pass: 工程管理实战's lesson 35 reached three identical parts that way (the platform
stores its title as `35. [测试体系]单元测试.mp4.mp4`, and stripping one extension matched
nothing). Nothing else in the pipeline notices -- biliup appends happily -- so the check has to
be its own step.

Three things are reported per collection:

  * duplicate part titles, which mean an append that should not have happened -- unless the
    copies have different durations, which means two lessons that legitimately share a title
    (前端工具链 has two `06. APIs`);
  * parts against the catalogue's own episode count, so a silent gap shows up: the uploader
    treats a lesson as present when a part has its title, and a missing lesson whose title is
    shared with a present one is never uploaded;
  * parts against the local file count, as a sanity check while an upload is in flight.

    工程管理实战 is the known duplicate and needs a human (issues/02): the delete endpoint 404s.
"""

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
STATUS = REPO / "verify_fresh" / "upload-status" / "courses.json"
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")

# Collections whose duplicate parts are known, tracked, and waiting on a human.
KNOWN_DUPLICATES = {"工程管理实战"}

sys.path.insert(0, str(REPO / "tools"))
import clean_work as cw  # noqa: E402


def catalogue_counts() -> dict:
    """course name -> episode count, only where the name is unambiguous."""
    document = json.loads(STATUS.read_text(encoding="utf-8"))
    counts = collections.Counter()
    for course in document["courses"]:
        counts[course["name"]] += 1
    return {course["name"]: course["episode_count"] for course in document["courses"]
            if counts[course["name"]] == 1}


def norm(title: str) -> str:
    t = re.sub(r"(\.(mp4|mkv|ts))+$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


def parts(bv: str) -> list:
    result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "show", bv],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    brace = result.stdout.find("{")
    if brace < 0:
        return []
    try:
        data = json.loads(result.stdout[brace:])
    except json.JSONDecodeError:
        return []
    return [(v.get("title", ""), v.get("duration")) for v in data.get("videos", [])]


def classify(titles: list) -> tuple:
    """(real duplicates, harmless title collisions).

    A repeated title is only evidence of a bad append when the copies are the same lesson, and
    duration says which it is: 前端工具链 legitimately carries two parts called `06. APIs` (第三章
    ESLint, 2729 s, and 第四章 Babel, 1526 s), while 工程管理实战's three `35. [测试体系]单元测试.mp4`
    parts are all 2281 s -- the same file appended three times.
    """
    grouped = collections.defaultdict(list)
    for title, duration in titles:
        grouped[norm(title)].append(duration)
    duplicates, collisions = {}, {}
    for title, durations in grouped.items():
        if len(durations) < 2:
            continue
        if len({round(float(d or 0)) for d in durations}) == 1:
            duplicates[title] = durations
        else:
            collisions[title] = durations
    return duplicates, collisions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()

    problems = 0
    shortfalls = []
    catalogue = catalogue_counts()
    print(f"{'collection':<34}{'parts':>6}{'cat':>5}{'local':>6}  note")
    print("-" * 96)
    for rel, bvs in cw.BILI_BY_COLLECTION.items():
        if not bvs:
            continue
        name = pathlib.PurePosixPath(rel).name
        if args.only and not any(key in rel for key in args.only):
            continue
        local = [p for p in (OUT / rel).rglob("*")
                 if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES] if (OUT / rel).is_dir() else []
        expected = catalogue.get(name)
        for bv in bvs:
            titles = parts(bv)
            if not titles:
                print(f"{name[:34]:<34}{'?':>6}{str(expected or '-'):>5}{len(local):>6}  {bv}: no data")
                continue
            duplicates, collisions = classify(titles)
            note = ""
            if duplicates:
                note = "DUPLICATE " + ", ".join(f"{t}×{len(d)}" for t, d in duplicates.items())
                if name not in KNOWN_DUPLICATES:
                    problems += 1
            if collisions:
                note += ("  " if note else "") + "title collision (different durations): " + \
                        ", ".join(f"{t} {[round(float(x or 0)) for x in d]}"
                                  for t, d in collisions.items())
            # Coverage only means something for a collection that is its whole course: WebGIS课程
            # is one catalogue course of 220 lessons across ten collections, so a chapter's count
            # has nothing to compare against and reads as "-".
            if expected and len(titles) < expected:
                gap = expected - len(titles)
                # A collection whose remaining lessons are still on disk is mid-upload, not short.
                if gap > len(local):
                    note += ("  " if note else "") + f"SHORT {gap} lesson(s) not in the 稿件"
                    if name not in KNOWN_SHORT:
                        shortfalls.append((name, bv, gap, len(local)))
            print(f"{name[:34]:<34}{len(titles):>6}{str(expected or '-'):>5}{len(local):>6}  "
                  f"{bv} {note}")
            for title, durations in duplicates.items():
                print(f"      ×{len(durations)}  {title}  durations={durations}")

    print()
    if shortfalls:
        # Informational, not a failure: while the publish backlog runs, most of a new collection
        # is neither in the 稿件 nor on disk yet. Which of these are waiting on an export and
        # which are lost is the question `tools/upload_gaps.py` answers from courses.json.
        print(f"{len(shortfalls)} collection(s) are below their catalogue count "
              f"(see upload_gaps.py for whether an export is still pending):")
        for name, bv, gap, have in shortfalls:
            print(f"  {name} ({bv}): {gap} lesson(s) neither in the 稿件 nor on disk, "
                  f"{have} on disk")
    if problems:
        print(f"{problems} collection(s) have duplicates nobody has accounted for")
        return 1
    print("no unaccounted duplicates"
          + (f"; {', '.join(sorted(KNOWN_DUPLICATES))} still needs a human" if KNOWN_DUPLICATES else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
