"""Classify every segment we hold a key for: where it sits in the lesson, and whether it decodes.

The grey files are not a property of a lesson. Lesson 119354 has segments that decode into a crisp
screen recording and segments that come out flat, and the difference has to be pinned to something
observable before any explanation is worth having. Three candidates are cheap to separate: position in
the lesson (pts), the encoder's own parameters (the SPS level byte), and the file itself.

So: for every key on file, find the file it names, decrypt it, read its first presentation timestamp,
read the SPS out of its elementary stream, and decode one frame to see whether a picture comes out.
The table is the experiment; whichever column separates the grey rows from the rest is the thing to
chase next.

    python classify_segments.py [--lesson 119354]
"""

import argparse
import glob
import json
import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "captured", "key_files.json")
DOWNLOADS = r"D:\Downloads\EVPlayer2Downloads"
WORK = os.path.join(HERE, "captured", "classify")


def load_keys():
    keys = {}
    for path in (os.path.join(HERE, "captured", "player_keys.jsonl"),
                 os.path.join(HERE, "captured", "pairing", "events.jsonl")):
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            try:
                record = json.loads(line)
            except Exception:
                continue
            if record.get("key"):
                keys.setdefault(record["key"], record.get("at", 0))
    return keys


def sps_of(blob):
    i = 0
    while i + 3 < len(blob):
        if blob[i] == 0 and blob[i + 1] == 0 and blob[i + 2] == 1 and (blob[i + 3] & 0x1F) == 7:
            return blob[i + 3:i + 3 + 32]
        i += 1
    return None


def frame_verdict(path):
    with tempfile.TemporaryDirectory() as work:
        raw = os.path.join(work, "f.gray")
        subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "select=eq(n\\,30)",
                        "-vsync", "0", "-pix_fmt", "gray", "-f", "rawvideo", "-y", raw],
                       capture_output=True)
        if not os.path.exists(raw) or os.path.getsize(raw) < 1920 * 1080:
            return None
        plane = open(raw, "rb").read(1920 * 1080)
        mean = statistics.fmean(plane)
        spread = statistics.pstdev(plane)
        return mean, spread


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="", help="only files whose name starts with this")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    sys.path.insert(0, HERE)
    from build_videos import decrypt, file_for_key, first_pts

    os.makedirs(WORK, exist_ok=True)
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    names = [n for n in os.listdir(DOWNLOADS) if n.endswith(".ts")]
    keys = load_keys()
    print(f"{len(keys)} key(s) on file, {len(names):,} file(s) downloaded\n")

    rows = []
    for key, when in sorted(keys.items(), key=lambda kv: kv[1]):
        name = cache.get(key)
        if name is None or not os.path.exists(os.path.join(DOWNLOADS, name)):
            name = file_for_key(key, names)
            cache[key] = name
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1)
        if not name:
            continue
        if args.lesson and not name.startswith(args.lesson):
            continue
        plain = decrypt(open(os.path.join(DOWNLOADS, name), "rb").read(), key, name)
        pts = first_pts(plain)
        sps = sps_of(plain)
        path = os.path.join(WORK, name + ".ts")
        if not os.path.exists(path):
            with open(path, "wb") as out:
                out.write(plain)
        verdict = frame_verdict(path)
        rows.append({
            "name": name,
            "pts": pts,
            "level": sps[3] if sps else None,
            "sps": sps[:8].hex(" ") if sps else "",
            "mean": verdict[0] if verdict else None,
            "sd": verdict[1] if verdict else None,
        })

    rows.sort(key=lambda row: (row["pts"] is None, row["pts"]))
    print(f"{'pts':>10}  {'sec':>8}  {'lvl':>4}  {'mean':>7}  {'sd':>6}  verdict   file")
    grey = picture = 0
    for row in rows:
        if row["mean"] is None:
            verdict = "no frame"
        elif row["sd"] < 3:
            verdict = "GREY"
            grey += 1
        else:
            verdict = "picture"
            picture += 1
        print(f"{row['pts'] if row['pts'] is not None else '-':>10}  "
              f"{(row['pts'] / 90000) if row['pts'] is not None else 0:8.0f}  "
              f"{row['level'] if row['level'] is not None else '-':>4}  "
              f"{row['mean'] if row['mean'] is not None else 0:7.1f}  "
              f"{row['sd'] if row['sd'] is not None else 0:6.2f}  {verdict:<9} {row['name'][:44]}")
        if args.limit and len(rows) >= args.limit:
            break
    print(f"\n{grey} grey, {picture} picture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
