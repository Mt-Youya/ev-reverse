"""Put the decrypted stretches and the recorded ones on one timeline and join them into one file.

Both halves carry the player's own timeline in their names: an assembled video is named for the PTS
seconds its first and last segment covered, and a recorded clip is named for the PTS second it started
at. So the two can be ordered together without either side knowing anything about the other, and the
result is a single file that follows the lesson -- pictures where decryption produced them, recorded
pictures where the player was the only source, each already carrying the decrypted audio.

The joined length is checked against the sum of the parts, which is the only way a piece dropped by the
concat demuxer would be noticed.

    python merge_timeline.py --dec D:\\ev-export\\lessons\\119354\\by-session\\<slug>\\119354 \\
                             --capture D:\\ev-export\\capture\\mp4\\muxed --out D:\\ev-export\\merged
"""

import argparse
import os
import re
import subprocess
import sys

DEC = re.compile(r"_(\d+)s-(\d+)s\.mp4$")
CAP = re.compile(r"stream_(\d+)_with_audio\.mp4$")


def duration_of(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float((done.stdout or "").strip())
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dec", required=True, help="directory of assembled videos for one session")
    ap.add_argument("--capture", required=True, help="directory of clips muxed with decrypted audio")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="lesson_merged.mp4")
    args = ap.parse_args()

    parts = []
    for name in sorted(os.listdir(args.dec)):
        match = DEC.search(name)
        if match:
            parts.append((int(match.group(1)), os.path.join(args.dec, name), "decrypted"))
    for name in sorted(os.listdir(args.capture)):
        match = CAP.search(name)
        if match:
            parts.append((int(match.group(1)), os.path.join(args.capture, name), "recorded"))
    if not parts:
        print("nothing to merge: no assembled video and no recorded clip was found")
        return 1
    parts.sort()

    os.makedirs(args.out, exist_ok=True)
    print(f"{len(parts)} piece(s), by the player's timeline:")
    for start, path, kind in parts:
        seconds = duration_of(path) or 0
        print(f"  {start:>7}s  {kind:<10} {seconds:7.2f}s  {os.path.basename(path)[:52]}")

    # Overlap is dropped rather than concatenated: a decrypted stretch and a recording can cover the
    # same seconds, and playing both would make the file longer than the lesson, which is the one
    # failure this project keeps measuring against.
    kept, last_end = [], -1
    for start, path, kind in parts:
        seconds = duration_of(path) or 0
        if start < last_end:
            print(f"  skipping {os.path.basename(path)[:44]} -- its seconds are already covered")
            continue
        kept.append((start, path, kind, seconds))
        last_end = start + seconds

    listing = os.path.join(args.out, "timeline.txt")
    with open(listing, "w", encoding="utf-8") as handle:
        for _, path, _, _ in kept:
            handle.write("file '" + path.replace("'", "'\\''") + "'\n")
    target = os.path.join(args.out, args.name)
    # The concat *demuxer* with stream copy assumes the pieces agree about time base and edit lists,
    # and these do not: one kind was remuxed out of a transport stream, the other encoded from raw
    # frames into a fresh MP4. Joining them that way lost 4.35 seconds of 53.59 -- which the length
    # check caught, and which is why the pieces are re-encoded into one format here instead. Slower,
    # and the decrypted picture is re-compressed once, but the file is the length it claims to be.
    done = subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing,
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt",
                           "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                           "-y", target], capture_output=True, text=True)
    os.remove(listing)
    if not os.path.exists(target):
        print(f"merge failed: {(done.stderr or '')[:200]}")
        return 1

    expected = sum(seconds for _, _, _, seconds in kept)
    actual = duration_of(target) or 0
    gaps = sum(1 for index in range(1, len(kept))
               if kept[index][0] > kept[index - 1][0] + kept[index - 1][3] + 1)
    print(f"\nmerged {len(kept)} piece(s): sum {expected:.2f}s, file {actual:.2f}s, "
          f"difference {actual - expected:+.2f}s")
    if gaps:
        print(f"{gaps} gap(s) in the timeline: seconds the lesson has that no piece covers")
    print(f"written {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
