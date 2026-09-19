"""Every lesson folder under a local root, with what R2 holds for it and what it lacks.

Scans the tree rather than a hand-written list of courses: an earlier pass compared only the folders
someone had thought to name, and reported a clean bill of health for a tree it had not finished
looking at. Nothing here hard-codes a folder name, so the Chinese ones never travel through a shell.

Pairing is Jaccard similarity over lesson numbers -- shared numbers over the union. Raw overlap would
call `01. x.mp4` a match for every course in the tree.

    python r2_audit.py [--root <dir>] [--json <out>]
"""

import argparse
import json
import pathlib
import re
import sys

VIDEO_SUFFIXES = (".mp4", ".mkv")
DEFAULT_ROOT = pathlib.Path(r"D:\Codes\github\ev-reverse\verify_fresh\out\AI 大全栈")
INVENTORY = pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-inventory.json")


def lesson_number(name: str):
    match = re.match(r"^\s*(\d+)", name)
    return int(match.group(1)) if match else None


def local_folders(root: pathlib.Path) -> dict:
    folders: dict[pathlib.Path, dict] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            number = lesson_number(path.name)
            if number is not None:
                folders.setdefault(path.parent, {})[number] = path
    return folders


def remote_folders() -> dict:
    entries = json.loads(INVENTORY.read_text(encoding="utf-8"))
    folders: dict[str, dict] = {}
    for entry in entries:
        prefix, _, name = entry["key"].rpartition("/")
        number = lesson_number(name)
        if number is not None and entry["size"] > 0:
            folders.setdefault(prefix + "/", {})[number] = entry
    return folders


def similarity(mine: set, theirs: set) -> float:
    union = mine | theirs
    return len(mine & theirs) / len(union) if union else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-audit.json"))
    args = parser.parse_args()

    locals_ = local_folders(args.root)
    remotes = remote_folders()
    print(f"scanned {args.root}")
    print(f"  {len(locals_)} local lesson folder(s), "
          f"{sum(len(v) for v in locals_.values())} videos")
    print(f"  {len(remotes)} R2 lesson folder(s), "
          f"{sum(len(v) for v in remotes.values())} videos\n")

    # Best R2 folder per local course, decided by similarity, each R2 folder used at most once.
    scored = {}
    for folder, lessons in locals_.items():
        mine = set(lessons)
        scored[folder] = sorted(((similarity(mine, set(r)), p) for p, r in remotes.items()),
                                reverse=True)

    used, rows = set(), []
    for folder in sorted(locals_, key=lambda f: -(scored[f][0][0] if scored[f] else 0)):
        best_similarity, best_prefix = scored[folder][0] if scored[folder] else (0.0, None)
        chosen = next((p for s, p in scored[folder] if s >= 0.4 and p not in used), None)
        if chosen:
            used.add(chosen)
        theirs = remotes.get(chosen, {}) if chosen else {}
        missing = sorted(set(locals_[folder]) - set(theirs))
        rows.append({
            "local": str(folder),
            "relative": str(folder.relative_to(args.root)),
            "lessons": len(locals_[folder]),
            "r2_prefix": chosen,
            "r2_count": len(theirs),
            "best_similarity": round(best_similarity, 3),
            "missing": [{"number": n, "file": locals_[folder][n].name,
                         "size": locals_[folder][n].stat().st_size} for n in missing],
        })

    print(f"{'local course':<46}{'loc':>4}{'R2':>4}{'sim':>6}  {'missing':<8} numbers")
    print("-" * 100)
    for row in rows:
        rel = row["relative"]
        label = rel if len(rel) <= 44 else "..." + rel[-41:]
        numbers = " ".join(str(m["number"]) for m in row["missing"])
        if len(numbers) > 46:
            numbers = numbers[:46] + " ..."
        prefix = row["r2_prefix"] or "(none)"
        print(f"{label:<46}{row['lessons']:>4}{row['r2_count']:>4}{row['best_similarity']:>6.2f}  "
              f"{prefix:<8} {numbers if numbers else '-- complete --'}")

    total_missing = sum(len(r["missing"]) for r in rows)
    total_bytes = sum(m["size"] for r in rows for m in r["missing"])
    print(f"\nmissing from R2: {total_missing} videos / {total_bytes / 1024 / 1024 / 1024:.2f} GB "
          f"across {sum(1 for r in rows if r['missing'])} course(s)")

    unmatched = [p for p in remotes if p not in used]
    if unmatched:
        print(f"\nR2 folders no local course matched ({len(unmatched)}):")
        for prefix in sorted(unmatched):
            print(f"   {prefix:<50} {len(remotes[prefix])} file(s)")

    args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
