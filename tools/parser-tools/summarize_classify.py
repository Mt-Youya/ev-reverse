"""Summarise classify_segments.py's table: which level and which verdict, by position.

    python summarize_classify.py captured/classify_all.txt
"""

import collections
import re
import sys

ROW = re.compile(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+(GREY|picture)\s+(\S+)")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "captured/classify_all.txt"
    blob = open(path, "rb").read()
    for encoding in ("utf-8", "utf-16", "utf-16-le"):
        try:
            text = blob.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        print("could not decode the file")
        return 1

    rows = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match:
            rows.append((int(match.group(1)), int(match.group(2)), int(match.group(3)),
                         float(match.group(4)), float(match.group(5)), match.group(6),
                         match.group(7)))
    print(f"{len(rows)} classified segment(s)\n")

    counts = collections.Counter((row[2], row[5]) for row in rows)
    print("level x verdict:")
    for (level, verdict), count in sorted(counts.items()):
        print(f"  level {level:<3} {verdict:<8} {count}")

    print("\nposition range per (level, verdict):")
    groups = collections.defaultdict(list)
    for row in rows:
        groups[(row[2], row[5])].append(row[1])
    for key in sorted(groups):
        values = sorted(groups[key])
        gaps = [b - a for a, b in zip(values, values[1:]) if b - a > 15]
        print(f"  level {key[0]:<3} {key[1]:<8} n={len(values):<4} {min(values)}s .. {max(values)}s"
              + (f"   ({len(gaps) + 1} stretches)" if gaps else ""))

    print("\nevery row, in position order:")
    for row in sorted(rows, key=lambda r: r[1]):
        print(f"  {row[1]:>5}s  level {row[2]:<3} sd={row[4]:7.2f}  {row[5]:<8} {row[6][:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
