"""Produce one lesson's worth of video per playback session, and say how each one measured up.

The file-name prefix is not the lesson: several playback sessions share it, each with its own list of
segments, its own PTS grid and its own `tk`. Assembling across them mixes lessons, which is how the
tool once reported a timeline running past the end of the list it came from. So the unit of work is
the session, and this runs the assembler once per session that has enough verified keys to be worth
assembling -- each invocation cheap, because the segment facts are cached -- and prints what each one
produced: positions covered, seconds of video, and how far each video's length is from the content it
was built from.

    python lesson_by_session.py --lesson 119354 --min-keys 4 --out D:\\ev-export\\lessons
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r"D:\Downloads\EVPlayer2Downloads"
LIBRARY = os.path.join(HERE, "captured", "keys_merged.json")
SESSIONS = os.path.join(HERE, "captured", "lesson_sessions.json")
SUMMARY = re.compile(r"assembled ([\d.]+)s across (\d+) video\(s\), covering (\d+) position")


def slug(label):
    return re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")[-40:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="119354")
    ap.add_argument("--min-keys", type=int, default=4)
    ap.add_argument("--max-sessions", type=int, default=8)
    ap.add_argument("--out", default=r"D:\ev-export\lessons")
    ap.add_argument("--min-segments", type=int, default=1)
    args = ap.parse_args()

    library = json.load(open(LIBRARY, encoding="utf-8"))
    sessions = json.load(open(SESSIONS, encoding="utf-8"))

    ranked = []
    for label, names in sessions.items():
        usable = [n for n in names if n in library
                  and os.path.exists(os.path.join(CACHE_DIR, n))]
        if len(usable) >= args.min_keys:
            ranked.append((len(usable), label, usable))
    ranked.sort(reverse=True)
    print(f"{len(ranked)} session(s) have at least {args.min_keys} usable segment(s); "
          f"processing {min(len(ranked), args.max_sessions)}")
    if not ranked:
        print("nothing to do: no session has enough verified keys on disk")
        return 0

    rows = []
    for count, label, usable in ranked[:args.max_sessions]:
        target = os.path.join(args.out, args.lesson, "by-session", slug(label))
        done = subprocess.run([sys.executable, os.path.join(HERE, "decrypt_lesson.py"),
                               "--lesson", args.lesson, "--session", label,
                               "--min-segments", str(args.min_segments),
                               "--out", os.path.join(args.out, args.lesson, "by-session")],
                              capture_output=True, text=True)
        match = SUMMARY.search(done.stdout or "")
        if not match:
            tail = [line for line in (done.stdout or "").splitlines() if line.strip()][-2:]
            rows.append({"session": label, "keys": count, "seconds": None, "videos": 0,
                         "positions": 0, "note": " | ".join(tail)[:90]})
            continue
        seconds, videos, positions = float(match.group(1)), int(match.group(2)), int(match.group(3))
        report = os.path.join(args.out, args.lesson, "report.json")
        worst = None
        if os.path.exists(report):
            entries = json.load(open(report, encoding="utf-8"))
            ratios = [abs(entry["ratio"] - 1) for entry in entries if entry.get("ratio")]
            if ratios:
                worst = max(ratios)
        rows.append({"session": label, "keys": count, "seconds": seconds, "videos": videos,
                     "positions": positions, "worst_ratio_deviation": worst, "note": ""})

    print(f"\n{'keys':>5}  {'secs':>8}  {'videos':>7}  {'pos':>5}  {'worst':>7}  session")
    for row in rows:
        seconds = f"{row['seconds']:.1f}" if row.get("seconds") is not None else "-"
        worst = f"{row['worst_ratio_deviation']:.4f}" if row.get("worst_ratio_deviation") else "-"
        print(f"{row['keys']:>5}  {seconds:>8}  {row['videos']:>7}  {row['positions']:>5}  "
              f"{worst:>7}  {row['session'][-46:]}"
              + (f"   {row['note']}" if row.get("note") else ""))
    total = sum(row["seconds"] or 0 for row in rows)
    print(f"\n{len(rows)} session(s), {total:.1f}s of video in total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
