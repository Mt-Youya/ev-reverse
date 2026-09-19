"""Pairs every local lesson folder with its R2 prefix by content, then reports what is missing.

The pairing used to come from a hand-written table of local folder -> R2 prefix. That table went stale
twice in one session: a folder was renamed between exports, and a course moved to a new prefix. Both
times the table's miss looked like "course complete" or "folder missing", not like a broken table.

So the mapping is derived instead. A local folder keeps the R2 prefixes of the audits and upload
runs already recorded in build/, and any folder with no recorded prefix is paired by lesson-number
overlap against every R2 folder. A folder that pairs with nothing is reported as unpaired rather
than as zero files.

    python r2_status.py [--root <dir>] [--min-similarity 0.4]
"""

import argparse
import json
import pathlib
import re
import sys

VIDEO_SUFFIXES = (".mp4", ".mkv")
DEFAULT_ROOT = pathlib.Path(r"D:\Codes\github\ev-reverse\verify_fresh\out\AI 大全栈")
INVENTORY = pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-inventory.json")
AUDIT = pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-audit.json")
COVER_PLAN = pathlib.Path(r"D:\Codes\github\ev-reverse\build\cover-plan.json")


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
        if entry["size"] == 0 or not entry["key"].lower().endswith(VIDEO_SUFFIXES):
            continue
        prefix, _, name = entry["key"].rpartition("/")
        number = lesson_number(name)
        if number is not None:
            folders.setdefault(prefix + "/", {})[number] = entry
    return folders


def recorded_prefixes() -> dict:
    """local folder -> prefix, from the audits and cover runs already on disk."""
    known: dict[str, str] = {}
    for path, key in ((AUDIT, "r2_prefix"), (COVER_PLAN, "prefix")):
        if not path.exists():
            continue
        try:
            for row in json.loads(path.read_text(encoding="utf-8")):
                local, prefix = row.get("local") or row.get("course"), row.get(key)
                if local and prefix:
                    known[local] = prefix
        except (ValueError, KeyError):
            continue
    return known


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    parser.add_argument("--min-similarity", type=float, default=0.4)
    args = parser.parse_args()

    locals_, remotes, known = local_folders(args.root), remote_folders(), recorded_prefixes()
    print(f"{len(locals_)} local folder(s), {sum(len(v) for v in locals_.values())} videos")
    print(f"{len(remotes)} R2 folder(s), {sum(len(v) for v in remotes.values())} videos\n")

    used, rows = set(), []
    # Recorded pairings first: they are what an earlier run actually uploaded to.
    for folder, lessons in locals_.items():
        prefix = known.get(str(folder))
        if not prefix or prefix in used or prefix not in remotes:
            continue
        used.add(prefix)
        rows.append((folder, lessons, prefix, remotes[prefix], "recorded"))

    # Everything else by similarity.
    for folder, lessons in locals_.items():
        if any(r[0] == folder for r in rows):
            continue
        mine = set(lessons)
        best, score = None, 0.0
        for prefix, theirs in remotes.items():
            if prefix in used:
                continue
            union = mine | set(theirs)
            similarity = len(mine & set(theirs)) / len(union) if union else 0.0
            if similarity > score:
                best, score = prefix, similarity
        if best and score >= args.min_similarity:
            used.add(best)
            rows.append((folder, lessons, best, remotes[best], f"matched {score:.2f}"))
        else:
            rows.append((folder, lessons, None, {}, "UNPAIRED"))

    print(f"{'local folder':<44}{'loc':>4}{'R2':>4}  {'how':<14}missing")
    print("-" * 104)
    gaps = {}
    for folder, lessons, prefix, theirs, how in rows:
        missing = sorted(set(lessons) - set(theirs))
        rel = str(folder.relative_to(args.root))
        label = rel if len(rel) <= 42 else "..." + rel[-39:]
        numbers = " ".join(str(n) for n in missing)
        if len(numbers) > 30:
            numbers = numbers[:30] + " ..."
        print(f"{label:<44}{len(lessons):>4}{len(theirs):>4}  {how:<14}{numbers or '-'}")
        if missing:
            gaps[rel] = {"prefix": prefix, "missing": [
                {"number": n, "name": lessons[n].name, "size": lessons[n].stat().st_size}
                for n in missing]}

    total = sum(len(g["missing"]) for g in gaps.values())
    size = sum(m["size"] for g in gaps.values() for m in g["missing"])
    print(f"\nmissing: {total} videos / {size / 1024 / 1024 / 1024:.2f} GB "
          f"across {len(gaps)} folder(s)")

    orphan = sorted(p for p in remotes if p not in used)
    if orphan:
        print(f"\nR2 folders no local folder matched ({len(orphan)}):")
        for prefix in orphan:
            print(f"   {prefix:<50} {len(remotes[prefix])} video(s)")

    pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-status.json").write_text(
        json.dumps(gaps, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
