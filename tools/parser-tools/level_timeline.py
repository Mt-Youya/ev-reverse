"""Line up every decrypted segment we hold: position, SPS level, and when it was downloaded.

`classify_segments.py` left its decrypted segments in `captured/classify/`, which is enough to read the
SPS out of each one without touching the player again. Three columns then answer which of them tracks
the difference between segments that decode and segments that come out flat: where the segment sits in
the lesson, what the encoder said about it, and when the player fetched it.

    python level_timeline.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "captured", "classify")
DOWNLOADS = r"D:\Downloads\EVPlayer2Downloads"

sys.path.insert(0, HERE)
from build_videos import first_pts                       # noqa: E402
from slice_probe import nals_of                          # noqa: E402


def sps_level(blob):
    for nal_type, body in nals_of(blob):
        if nal_type == 7 and len(body) > 3:
            return body[3], body[:8]
    return None, None


def main():
    if not os.path.isdir(WORK):
        print("no decrypted segments in captured/classify -- run classify_segments.py first")
        return 1
    rows = []
    for name in sorted(os.listdir(WORK)):
        if not name.endswith(".ts"):
            continue
        blob = open(os.path.join(WORK, name), "rb").read()
        level, head = sps_level(blob)
        pts = first_pts(blob)
        original = os.path.join(DOWNLOADS, name[:-3])          # strip the extra .ts
        stat = os.stat(original) if os.path.exists(original) else None
        rows.append({
            "name": name,
            "pts": pts,
            "level": level,
            "head": head.hex(" ") if head else "",
            "created": stat.st_ctime if stat else None,
            "written": stat.st_mtime if stat else None,
            "size": stat.st_size if stat else None,
        })
    rows.sort(key=lambda row: (row["pts"] is None, row["pts"] or 0))

    print(f"{'sec':>7}  {'lvl':>4}  {'size':>9}  {'downloaded':<20}  file")
    import datetime
    for row in rows:
        when = (datetime.datetime.fromtimestamp(row["written"]).strftime("%m-%d %H:%M:%S")
                if row["written"] else "-")
        print(f"{(row['pts'] or 0) / 90000:7.0f}  {row['level'] if row['level'] else '-':>4}  "
              f"{row['size'] or 0:>9,}  {when:<20}  {row['name'][:44]}")

    print("\nby level: position range and download window")
    groups = {}
    for row in rows:
        groups.setdefault(row["level"], []).append(row)
    for level in sorted(groups, key=lambda value: (value is None, value)):
        group = groups[level]
        seconds = [row["pts"] / 90000 for row in group if row["pts"]]
        times = sorted(row["written"] for row in group if row["written"])
        window = "-"
        if times:
            window = (f"{datetime.datetime.fromtimestamp(times[0]):%m-%d %H:%M} .. "
                      f"{datetime.datetime.fromtimestamp(times[-1]):%m-%d %H:%M}")
        print(f"  level {level}: n={len(group):<4} "
              f"{min(seconds):.0f}s..{max(seconds):.0f}s   written {window}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
