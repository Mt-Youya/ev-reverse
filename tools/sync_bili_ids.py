"""Fill in the 稿件 ids that `clean_work.BILI_BY_COLLECTION` does not know yet.

`bili_upload.py` finds a collection's 稿件 by title from `biliup list`, so it needs nothing
recorded. Two other readers do: the status page asks this table whether a lesson is on
Bilibili, and `verify_and_delete.py` reads the part durations through it before deleting a
local file. A 稿件 created today is invisible to both until its id is written down -- and an
empty list is a positive claim ("nothing holds these lessons"), not a gap to be filled at
random.

Matching is by title, and only entries that are empty are touched, because titles are not
reliable identifiers: `Python语言核心精讲`'s 稿件 is titled `Python语言基础`, and 音视频实时互动技术
has two 稿件s carrying the same title (the watermarked one is in bili_upload.SUPERSEDED).
Anything ambiguous is printed for a human instead of guessed.

    python sync_bili_ids.py [--apply]
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
TABLE = REPO / "tools" / "clean_work.py"

sys.path.insert(0, str(REPO / "tools"))
import clean_work as cw  # noqa: E402
import bili_upload as bu  # noqa: E402


def archives() -> dict:
    """稿件 title -> [(bv, part count)], from the account's own list."""
    result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "list", "--max-pages", "60"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [line for line in result.stdout.splitlines() if line.startswith("BV")]
    if not lines:
        # Bilibili answers the archives endpoint with HTTP 412 when it decides the caller is
        # asking too often, and biliup reports that as "Unknown Error / error decoding response
        # body". An empty listing is not "this account has no 稿件" -- it has hundreds -- so
        # reporting every collection as having none would be a lie, and in bili_upload's case a
        # costly one.
        raise SystemExit("biliup list returned no 稿件 at all (HTTP 412 from Bilibili?); "
                         "refusing to report from an empty listing")
    found = {}
    for line in lines:
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        bv, title = fields[0], fields[1].strip()
        if bv in bu.SUPERSEDED:
            continue
        found.setdefault(title, []).append(bv)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    listed = archives()
    known = set(cw.BILI_BY_COLLECTION)
    empty = [rel for rel in cw.R2_BY_COLLECTION
             if not cw.BILI_BY_COLLECTION.get(rel)]
    print(f"{len(listed)} 稿件(s) on the account, {len(empty)} collection(s) with no id recorded")

    filled, ambiguous, missing = {}, {}, []
    for rel in empty:
        title = pathlib.PurePosixPath(rel).name
        candidates = listed.get(title, [])
        if len(candidates) == 1:
            filled[rel] = candidates[0]
        elif len(candidates) > 1:
            ambiguous[rel] = candidates
        else:
            missing.append(rel)

    for rel, bv in sorted(filled.items()):
        print(f"  {pathlib.PurePosixPath(rel).name:<28} -> {bv}")
    for rel, bvs in sorted(ambiguous.items()):
        print(f"  ambiguous: {rel} matches {bvs} -- record by hand")
    for rel in sorted(missing):
        print(f"  no 稿件 titled {pathlib.PurePosixPath(rel).name!r} yet: {rel}")
    if known - set(cw.R2_BY_COLLECTION):
        print("  entries in the table that are no longer collections:",
              sorted(known - set(cw.R2_BY_COLLECTION)))

    if not filled:
        print("\nnothing to record")
        return 0
    if not args.apply:
        print(f"\n{len(filled)} id(s) to record -- dry run (pass --apply)")
        return 0

    source = TABLE.read_text(encoding="utf-8")
    for rel, bv in filled.items():
        pattern = re.compile(r'^(\s*)"' + re.escape(rel) + r'": \[\],$', re.M)
        source, count = pattern.subn(r'\1"' + rel.replace("\\", "\\\\") + f'": ["{bv}"],', source)
        if count != 1:
            sys.exit(f"expected exactly one empty entry for {rel}, patched {count}")
    TABLE.write_text(source, encoding="utf-8")
    print(f"\nwrote {len(filled)} id(s) into {TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
