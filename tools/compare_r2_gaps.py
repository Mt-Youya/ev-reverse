"""Every R2 folder next to every local course, with the lesson numbers R2 is missing.

The earlier version only compared the courses I had already guessed a prefix for, which is how
`videos/python/framework/duyi-edu/` came back "20 of 26" without naming 14, 16, 17, 20, 22 and 23.
This one starts from the full R2 listing and the full local walk, so a course is reported whether or
not anyone thought to pair it.

Pairing is by Jaccard similarity over lesson numbers, because `01. x.mp4` is in nearly every course
and raw overlap makes any folder look like a match. An R2 folder that pairs with nothing is listed as
such rather than being silently dropped.

Local folders are discovered by walking and R2 folders by listing; no folder name is hard-coded, so
the Chinese names never have to survive a trip through the shell.
"""

import json
import pathlib
import re
import sys

VIDEO_SUFFIXES = (".mp4", ".mkv")
ROOT = pathlib.Path(r"D:\Codes\github\ev-reverse\verify_fresh\out")
INVENTORY = pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-inventory.json")
OUT = pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-gaps.json")


def lesson_number(name: str):
    match = re.match(r"^\s*(\d+)\s*[.\-_]?\s*\d*", name)
    return int(match.group(1)) if match else None


def local_folders() -> dict:
    folders: dict[pathlib.Path, dict] = {}
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            number = lesson_number(path.name)
            if number is not None:
                folders.setdefault(path.parent, {})[number] = path
    return folders


def remote_objects() -> list:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def remote_folders(entries: list) -> dict:
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
    entries = remote_objects()
    locals_, remotes = local_folders(), remote_folders(entries)

    print("=" * 108)
    print("LOCAL COURSES: what R2 is missing (by lesson number)")
    print("=" * 108)
    print(f"{'local folder':<52}{'loc':>4}{'R2':>4}  missing lesson numbers")
    print("-" * 108)

    pairs, used, gaps = [], set(), {}
    scored_all = {}
    for folder, lessons in locals_.items():
        mine = set(lessons)
        scored = sorted(((similarity(mine, set(r)), p) for p, r in remotes.items()), reverse=True)
        scored_all[folder] = scored
    for folder in sorted(locals_, key=lambda f: -(scored_all[f][0][0] if scored_all[f] else 0)):
        chosen = next((p for s, p in scored_all[folder] if s >= 0.4 and p not in used), None)
        if chosen:
            used.add(chosen)
        pairs.append((folder, locals_[folder], chosen))

    for folder, lessons, prefix in pairs:
        theirs = remotes.get(prefix, {}) if prefix else {}
        missing = sorted(set(lessons) - set(theirs))
        rel = str(folder.relative_to(ROOT))
        label = rel if len(rel) <= 50 else "..." + rel[-47:]
        listed = " ".join(f"{n}" for n in missing[:12]) + (" ..." if len(missing) > 12 else "")
        print(f"{label:<52}{len(lessons):>4}{len(theirs):>4}  {listed if missing else '-- complete --'}")
        if missing:
            gaps[rel] = {"r2_prefix": prefix, "missing": [
                {"number": n, "file": locals_[folder][n].name,
                 "size": locals_[folder][n].stat().st_size} for n in missing]}

    print("\n" + "=" * 108)
    print("EVERY R2 FOLDER UNDER videos/: what it holds")
    print("=" * 108)
    owner = {p: f for f, _, p in pairs if p}
    for prefix in sorted(remotes):
        theirs = remotes[prefix]
        holder = owner.get(prefix)
        if holder:
            state = "paired with local course"
        else:
            state = "NO LOCAL COURSE HAS THESE NUMBERS"
        numbers = sorted(theirs)
        print(f"{prefix:<50}{len(theirs):>4} files   {state}")
        print(f"{'':<50}          numbers: {numbers}")

    print("\n" + "=" * 108)
    print("R2 PREFIX FOLDERS THAT HOLD NO VIDEO AT ALL (empty placeholders)")
    print("=" * 108)
    holders = {e["key"].rsplit("/", 1)[0] + "/" for e in entries}
    for entry in sorted(entries, key=lambda e: e["key"]):
        if entry["size"] == 0 and entry["key"].endswith("/"):
            print(f"   {entry['key']}")

    total_local = sum(len(v) for v in locals_.values())
    total_remote = sum(len(v) for v in remotes.values())
    missing_total = sum(len(g["missing"]) for g in gaps.values())
    missing_bytes = sum(m["size"] for g in gaps.values() for m in g["missing"])
    print("\n" + "=" * 108)
    print(f"local {total_local} videos in {len(locals_)} folders")
    print(f"R2    {total_remote} videos in {len(remotes)} folders")
    print(f"missing from R2: {missing_total} videos, {missing_bytes / 1024 / 1024 / 1024:.2f} GB "
          f"across {len(gaps)} courses")

    OUT.write_text(json.dumps(gaps, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
