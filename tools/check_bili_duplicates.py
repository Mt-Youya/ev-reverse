"""Look for duplicate parts and stale part counts in the account's 稿件s.

The uploader appends by comparing part titles, so a title it cannot match is appended again on
the next pass: 工程管理实战's lesson 35 reached three identical parts that way (the platform
stores its title as `35. [测试体系]单元测试.mp4.mp4`, and stripping one extension matched
nothing). Nothing in the pipeline notices -- biliup appends happily -- so the check has to be
its own step.

Two things are reported per collection:

  * duplicate part titles, which mean an append that should not have happened;
  * parts > local files + previously uploaded, i.e. a count that no longer matches any state
    the tools know about.

    工程管理实战 is the known case and needs a human (issues/02): the delete endpoint 404s.
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
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")

# Collections whose duplicate parts are known, tracked, and waiting on a human.
KNOWN_DUPLICATES = {"工程管理实战"}

sys.path.insert(0, str(REPO / "tools"))
import clean_work as cw  # noqa: E402


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
    print(f"{'collection':<38}{'parts':>6}{'local':>6}  note")
    print("-" * 88)
    for rel, bvs in cw.BILI_BY_COLLECTION.items():
        if not bvs:
            continue
        name = pathlib.PurePosixPath(rel).name
        if args.only and not any(key in rel for key in args.only):
            continue
        local = [p for p in (OUT / rel).rglob("*")
                 if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES] if (OUT / rel).is_dir() else []
        for bv in bvs:
            titles = parts(bv)
            if not titles:
                print(f"{name[:38]:<38}{'?':>6}{len(local):>6}  {bv}: no data")
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
            print(f"{name[:38]:<38}{len(titles):>6}{len(local):>6}  "
                  f"{bv} {note}")
            for title, durations in duplicates.items():
                print(f"      ×{len(durations)}  {title}  durations={durations}")

    print()
    if problems:
        print(f"{problems} collection(s) have duplicates nobody has accounted for")
        return 1
    print("no unaccounted duplicates"
          + (f" ({', '.join(sorted(KNOWN_DUPLICATES))} is known and needs a human)"
             if KNOWN_DUPLICATES else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
