"""Compare the player's decoder input with our decryption, NAL unit by NAL unit.

Comparing the two as raw streams answers the wrong question, because a frame's bytes do not sit at a
fixed offset in a transport stream and P frames share long common prefixes -- an earlier comparison
located a frame at a plausible-looking offset that was simply another frame with the same opening
bytes, and the "31-byte prefix then divergence" it reported was that mistake, not a transform.

NAL units are the right unit of comparison: their sequence and their lengths are structural, so
alignment can be *verified* rather than assumed. If the lengths line up one for one, the two streams
describe the same frames, and any byte that differs differs because something transformed it. Then
the per-type result says which parts of the stream are affected -- and a transform that spares the
NAL and slice headers while touching the slice data looks exactly like the error pattern the
decoders report at `MB 1 0`.

    python analyze_nals.py [--captured captured/pairing]
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

NAMES = {1: "non-IDR", 5: "IDR", 6: "SEI", 7: "SPS", 8: "PPS", 9: "AUD"}


def nals(stream):
    """Annex B NAL units as (type, payload), including the header byte."""
    starts = []
    index = 0
    while index < len(stream) - 3:
        if stream[index] == 0 and stream[index + 1] == 0 and stream[index + 2] == 1:
            starts.append(index + 3)
            index += 3
        else:
            index += 1
    for position, start in enumerate(starts):
        end = starts[position + 1] - 3 if position + 1 < len(starts) else len(stream)
        while end > start and stream[end - 1] == 0:
            end -= 1
        if end > start:
            yield stream[start] & 0x1F, stream[start:end]


def agreement(left, right):
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captured", default=os.path.join(HERE, "captured", "pairing"))
    ap.add_argument("--limit", type=int, default=4)
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
    print(f"{len(keys)} key(s), {len(frames)} frame(s) with data", flush=True)
    if not keys or not frames:
        return 1

    names = [name for name in os.listdir(CACHE) if name.endswith(".ts")]
    shown = 0
    for index, key in enumerate(keys):
        stop = keys[index + 1]["at"] if index + 1 < len(keys) else float("inf")
        window = [f for f in frames if key["at"] <= f["at"] < stop]
        if len(window) < 5:
            continue
        name = file_for_key(key["key"], names)
        if not name:
            continue
        plain = decrypt(open(os.path.join(CACHE, name), "rb").read(), key["key"], name)
        raw = os.path.join(args.captured, name + ".ts")
        with open(raw, "wb") as handle:
            handle.write(plain)
        es = raw + ".h264"
        subprocess.run(["ffmpeg", "-v", "error", "-i", raw, "-c:v", "copy", "-f", "h264", "-y", es],
                       capture_output=True)
        if not os.path.exists(es):
            continue
        ours = list(nals(open(es, "rb").read()))
        player_stream = b"".join(open(os.path.join(args.captured, f["file"]), "rb").read()
                                 for f in window)
        theirs = list(nals(player_stream))
        if not ours or not theirs:
            continue

        # Align by the sequence of (type, length): the player began mid-segment, so its first NAL
        # corresponds to some NAL of ours, and only a position where every length matches is real.
        best = None
        for start in range(len(ours)):
            if start + len(theirs) > len(ours):
                break
            same = sum(1 for a, b in zip(ours[start:start + len(theirs)], theirs)
                       if a[0] == b[0] and len(a[1]) == len(b[1]))
            if best is None or same > best[1]:
                best = (start, same)
        if not best:
            continue
        start, same = best
        print(f"\n{name}  {len(window)} frame(s), {len(theirs)} NAL(s) vs ours from index {start}, "
              f"{same}/{len(theirs)} lengths match", flush=True)
        if same < len(theirs) * 0.9:
            print("  alignment is not trustworthy, skipping", flush=True)
            continue
        by_type = {}
        for offset, (mine, other) in enumerate(zip(ours[start:start + len(theirs)], theirs)):
            if mine[0] != other[0] or len(mine[1]) != len(other[1]):
                continue
            agreed = agreement(mine[1], other[1])
            entry = by_type.setdefault(mine[0], [0, 0, 0])
            entry[0] += 1
            entry[1] += agreed
            entry[2] += len(mine[1])
            if agreed < len(mine[1]) and entry[0] <= 2:
                stream = bytes(a ^ b for a, b in zip(mine[1][agreed:agreed + 24],
                                                     other[1][agreed:agreed + 24]))
                print(f"  NAL {NAMES.get(mine[0], mine[0])} len {len(mine[1])}: agrees {agreed}, "
                      f"xor from there {stream.hex()}", flush=True)
        for kind, (count, agreed, total) in sorted(by_type.items()):
            print(f"  {NAMES.get(kind, kind):8s} {count:4d} NAL(s), "
                  f"{agreed / max(1, total) * 100:5.1f}% of bytes identical", flush=True)
        shown += 1
        if shown >= args.limit:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
