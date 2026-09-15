"""Find where the player's decoder input starts to differ from what we decrypt.

The three decoders available here (libavcodec software, NVIDIA cuvid, and QSV/AMF) all fail on some
lessons' segments, always at the same place -- `MB 1 0`, the second macroblock of the slice the
segment opens with -- while the player renders the same lesson normally. Since a frame captured from
the player's decoder has been found byte-for-byte inside our own decryption, the data is not wholly
transformed; something is going on in part of it, and the error position says which part.

This pairs keys with the frames that follow them, takes the first frame of each segment (the one
that begins with the segment's IDR), and reports how many leading bytes agree with our elementary
stream, where they stop agreeing, and whether the two are even the same length. A common prefix that
ends at a fixed offset is a transform over a region, not a wrong key.

    python compare_idr.py [--captured captured/pairing]
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = r"D:\Downloads\EVPlayer2Downloads"
sys.path.insert(0, HERE)

from build_videos import decrypt, file_for_key  # noqa: E402


def elementary_stream(plain, workdir, tag):
    raw = os.path.join(workdir, tag + ".ts")
    with open(raw, "wb") as handle:
        handle.write(plain)
    es = raw + ".h264"
    subprocess.run(["ffmpeg", "-v", "error", "-i", raw, "-c:v", "copy", "-f", "h264", "-y", es],
                   capture_output=True)
    return open(es, "rb").read() if os.path.exists(es) else b""


def common_prefix(left, right):
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captured", default=os.path.join(HERE, "captured", "pairing"))
    ap.add_argument("--limit", type=int, default=6)
    args = ap.parse_args()

    events = []
    with open(os.path.join(args.captured, "events.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    keys = [e for e in events if e["t"] == "key"]
    frames = [e for e in events if e["t"] == "frame" and e.get("file")]
    if not keys or not frames:
        print("no keys or no frames captured")
        return 1

    names = [name for name in os.listdir(CACHE) if name.endswith(".ts")]
    print(f"{len(keys)} key(s), {len(frames)} frame(s) with data", flush=True)

    shown = 0
    for index, key in enumerate(keys):
        following = [f for f in frames if f["at"] >= key["at"]]
        if not following:
            continue
        stop = keys[index + 1]["at"] if index + 1 < len(keys) else float("inf")
        segment_frames = [f for f in following if f["at"] < stop]
        if not segment_frames:
            continue
        name = file_for_key(key["key"], names)
        if not name:
            continue
        plain = decrypt(open(os.path.join(CACHE, name), "rb").read(), key["key"], name)
        es = elementary_stream(plain, args.captured, name)
        # The segment's own frames, largest first: a segment opens with an IDR, which is far larger
        # than the P frames that follow it.
        biggest = max(segment_frames, key=lambda f: f["size"])
        blob = open(os.path.join(args.captured, biggest["file"]), "rb").read()
        offset = es.find(blob[:16])
        prefix = common_prefix(es[offset:], blob) if offset >= 0 else 0
        print(f"\n{name}  ({len(segment_frames)} frame(s) before the next key)", flush=True)
        print(f"  our elementary stream {len(es)} B, player frame {len(blob)} B", flush=True)
        print(f"  frame starts at offset {offset if offset >= 0 else 'NOT FOUND'} in the stream",
              flush=True)
        if offset >= 0:
            print(f"  bytes agreeing from there: {prefix} of {len(blob)} "
                  f"({prefix / len(blob) * 100:.1f}%)", flush=True)
            if 0 < prefix < len(blob):
                print(f"  first divergence at +{prefix}", flush=True)
                print(f"    ours   {es[offset + prefix:offset + prefix + 16].hex()}", flush=True)
                print(f"    player {blob[prefix:prefix + 16].hex()}", flush=True)
        shown += 1
        if shown >= args.limit:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
