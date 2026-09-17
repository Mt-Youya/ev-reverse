"""Print the records watch_segment.py wrote, so the level/quality correlation is readable.

    python read_watch.py captured/watch/watch.jsonl
"""

import json
import sys


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "captured/watch/watch.jsonl"
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    print(f"{len(rows)} record(s) in {path}\n")
    keys = 0
    for row in rows:
        if row.get("t") == "key":
            keys += 1
            print(f"  key {row['key']}")
            continue
        stats = row.get("stats") or {}
        mean = stats.get("mean")
        sd = stats.get("sd")
        verdict = ""
        if sd is not None:
            verdict = "FLAT GREY" if sd < 3 else "picture"
        print(f"  pts={row['pts'] / 90000:8.1f}s  size={row['size']:>8,}  level={row.get('level')}"
              f"  idr={row.get('idr')}  ret={row.get('retval')}"
              f"  mean={mean if mean is None else round(mean, 1)}"
              f"  sd={sd if sd is None else round(sd, 2)}  {verdict}")
    print(f"\n{keys} key(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
