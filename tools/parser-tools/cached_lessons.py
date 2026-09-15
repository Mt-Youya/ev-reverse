"""Which captured segment lists can be processed from what is already on disk.

The offline path is: a list response (host + one entry per segment with `idx`, `sf`, `tk`), the
ciphertext for those segments, and nothing else. The player's download directory usually still
holds the ciphertext, so this reports per list how much of it is present — the question to ask
before running `evmedia derive`.

    python cached_lessons.py [--cache D:\\Downloads\\EVPlayer2Downloads]
"""

import argparse
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LIST_PATTERN = re.compile(r'\{"d_p":"([^"]+)","k_l":(\[.*?\}\])\}')


def lists(directory):
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        text = open(path, encoding="utf-8", errors="replace").read()
        for match in LIST_PATTERN.finditer(text):
            try:
                entries = json.loads(match.group(2))
            except Exception:
                continue
            if entries:
                yield path, match.start(), match.group(1), entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=r"D:\Downloads\EVPlayer2Downloads")
    ap.add_argument("--lists", default=os.path.join(HERE, "captured", "inflated"))
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    cache = set(os.listdir(args.cache)) if os.path.isdir(args.cache) else set()
    print(f"{len(cache)} file(s) in {args.cache}")
    rows = []
    for path, offset, host, entries in lists(args.lists):
        files = [os.path.basename(entry["sf"].split("?")[0]) for entry in entries]
        present = sum(1 for name in files if name in cache)
        indexes = sorted(entry["idx"] for entry in entries)
        rows.append((present, len(files), indexes == list(range(len(indexes))),
                     path, offset, host, indexes[0] if indexes else 0))
    rows.sort(reverse=True)
    for present, total, contiguous, path, offset, host, first in rows[:args.top]:
        print(f"  {present:3d}/{total:3d} segments  contiguous={contiguous}  first_idx={first}  "
              f"{os.path.basename(path)}@{offset}  {host[:58]}")
    complete = [row for row in rows if row[0] == row[1] and row[1] > 4]
    print(f"\n{len(rows)} list(s); {len(complete)} fully present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
