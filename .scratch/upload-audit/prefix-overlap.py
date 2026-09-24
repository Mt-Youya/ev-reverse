"""Check that each course's mapped R2 prefix really holds that course's lessons.

The tables in clean_work feed the R2 uploader and the status page, so a prefix that is
wrong -- or empty while the course's other lessons sit somewhere else -- would split a
course across two prefixes without anything failing. Compare by normalized title, which
is what the uploader matches on, and print the best-scoring prefix when it is not the
mapped one.

    python .scratch/upload-audit/prefix-overlap.py [--json <path>]
"""

import argparse
import collections
import json
import pathlib
import re
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
KEYS = REPO / "build" / "r2-keys.json"
COURSES = REPO / "verify_fresh" / "upload-status" / "courses.json"


def norm(title: str) -> str:
    t = re.sub(r"(\.(mp4|mkv|ts))+$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


def remote_titles() -> dict:
    keys = json.loads(KEYS.read_text(encoding="utf-8"))
    prefixes = collections.defaultdict(set)
    for key in keys:
        if "/封面/" in key or key.endswith("/"):
            continue
        prefix, _, name = key.rpartition("/")
        prefixes[prefix + "/"].add(norm(name))
    return prefixes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=pathlib.Path)
    args = parser.parse_args()

    prefixes = remote_titles()
    document = json.loads(COURSES.read_text(encoding="utf-8"))

    print(f"{'course':<34}{'eps':>5}  {'mapped prefix':<50}{'hit':>5}{'ovl':>6}")
    print("-" * 112)
    rows = []
    for course in document["courses"]:
        episodes = course["episodes"]
        if not episodes:
            continue
        titles = {norm(e["title"]) for e in episodes}
        mapped = course["r2_prefix"]
        mapped = mapped[0] if isinstance(mapped, list) else mapped
        have = len(titles & prefixes.get(mapped or "", set()))
        scored = sorted(((len(titles & s) / max(1, len(titles | s)), p, len(titles & s))
                         for p, s in prefixes.items()), reverse=True)
        best = scored[0] if scored else (0.0, None, 0)
        rows.append({"course": course["name"], "episodes": len(titles),
                     "mapped": mapped, "hit": have, "overlap": round(have / len(titles), 3),
                     "best": best[1], "best_hit": best[2],
                     "best_overlap": round(best[0], 3)})
        flag = "" if best[1] == mapped else "  <-- differs"
        print(f"{course['name'][:34]:<34}{len(titles):>5}  {str(mapped)[:50]:<50}"
              f"{have:>5}{have / len(titles):>6.2f}{flag}")

    print()
    print("rows whose mapped prefix is not the best match:")
    for row in rows:
        if row["mapped"] != row["best"]:
            print(f"  {row['course'][:34]:<34} mapped={str(row['mapped'])[:44]:<44}"
                  f"({row['hit']}/{row['episodes']})  best={str(row['best'])[:44]}"
                  f" ({row['best_hit']}/{row['episodes']})")

    incompletely = [r for r in rows if r["mapped"] and r["hit"] < r["episodes"]]
    print()
    print(f"{len(incompletely)} course(s) whose mapped prefix holds only part of the course")
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
