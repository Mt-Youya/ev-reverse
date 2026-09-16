"""Check, byte for byte, whether the player's decoder input is what we decrypted.

`captured/pairing/` holds two sides of the same moment: the payload of every packet the player
handed to `h264_decode_frame` (`*.bin`), and the elementary stream we extracted from the segment
each key named (`*.h264`). An earlier pass compared these by aligning NALs by length, which is a
heuristic -- equal-length NALs can be aligned while their bytes differ. This does the exact test
instead, in both directions:

  forward  every captured packet: is the whole packet present verbatim in one of our streams?
  backward every NAL >=256 B: is it present verbatim within one captured packet?

Missing matches can also reflect incomplete capture. A middle needle match is reported separately
and never counts as full identity. Smaller NALs are explicitly skipped.

    python verify_packet_identity.py [--pairing path]
"""

import argparse
import collections
import os
from pathlib import Path
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRING = os.path.join(HERE, "captured", "pairing")


def annex_b_nals(es):
    starts = list(re.finditer(rb"\x00{2,}\x01", es))
    out = []
    for index, start in enumerate(starts):
        body = start.end()
        end = starts[index + 1].start() if index + 1 < len(starts) else len(es)
        if end > body:
            out.append((es[body] & 0x1F, body, end - body))
    return out


def nal_identity(payload, blobs):
    """A needle finds candidates; only the complete NAL proves identity within one packet."""
    middle = len(payload) // 2
    needle = payload[middle:middle + 64]
    needle_found = False
    for blob in blobs:
        if needle in blob:
            needle_found = True
            if payload in blob:
                return "identical"
    return "needle-only" if needle_found else "absent"


def bucket(size):
    if size < 256:
        return "0-255"
    if size < 1024:
        return "256-1k"
    if size < 4096:
        return "1k-4k"
    if size < 16384:
        return "4k-16k"
    if size < 65536:
        return "16k-64k"
    return "64k+"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairing", default=PAIRING)
    args = ap.parse_args()
    if not os.path.isdir(args.pairing):
        print("no pairing directory -- pass --pairing or run pair_playback.py first")
        return 1
    streams = {}
    for name in sorted(os.listdir(args.pairing)):
        if name.endswith(".h264"):
            streams[name] = Path(args.pairing, name).read_bytes()
    if not streams:
        print("no .h264 files in captured/pairing -- run pair_playback.py first")
        return 1
    total = sum(len(v) for v in streams.values())
    print(f"{len(streams)} stream(s), {total:,} bytes total\n")

    bins = sorted(n for n in os.listdir(args.pairing) if n.endswith(".bin"))
    blobs = {name: Path(args.pairing, name).read_bytes() for name in bins}
    if not blobs:
        print("no captured .bin packets -- identity cannot be checked")
        return 1
    print(f"{len(blobs)} captured packet(s), {sum(map(len, blobs.values())):,} payload bytes\n")

    # forward: is each captured packet one of our bytes?
    exact = prefix = missing = 0
    forward_buckets = collections.defaultdict(lambda: [0, 0])
    for name in bins:
        blob = blobs[name]
        if len(blob) < 96:
            continue
        needle = blob[len(blob) // 2:len(blob) // 2 + 64]
        found = False
        for stream in streams.values():
            start = stream.find(needle)
            while start != -1:
                begin = start - len(blob) // 2
                if begin >= 0 and stream[begin:begin + len(blob)] == blob:
                    found = True
                    break
                start = stream.find(needle, start + 1)
            if found:
                break
        if found:
            exact += 1
        elif any(needle in s for s in streams.values()):
            prefix += 1
        else:
            missing += 1
        forward_buckets[bucket(len(blob))][0 if found else 1] += 1

    print(f"forward  packets checked {exact + prefix + missing}"
          f"  ->  identical {exact}, needle-only {prefix}, absent {missing}")
    print("  by packet size (identical / not):")
    for key in ("0-255", "256-1k", "1k-4k", "4k-16k", "16k-64k", "64k+"):
        if key in forward_buckets:
            same, other = forward_buckets[key]
            print(f"    {key:>8}  {same:5d} / {other:5d}")

    # backward: is each NAL of each stream among the captured packets?
    print("\nbackward  per-stream NAL coverage:")
    totals = collections.Counter()
    for name, stream in streams.items():
        nals = annex_b_nals(stream)
        covered = collections.defaultdict(collections.Counter)
        skipped = 0
        for ntype, offset, size in nals:
            if size < 256:
                skipped += 1
                continue
            result = nal_identity(stream[offset:offset + size], blobs.values())
            covered[bucket(size)][result] += 1
            totals[result] += 1
            if ntype == 5:
                print(f"  IDR {name}: offset={offset} size={size} {result}")
        parts = []
        for key in ("0-255", "256-1k", "1k-4k", "4k-16k", "16k-64k", "64k+"):
            if key in covered:
                counts = covered[key]
                parts.append(f"{key}:{counts['identical']}/{sum(counts.values())}"
                             f" (needle-only={counts['needle-only']}, absent={counts['absent']})")
        print(f"  {name[:52]:<52} nals={len(nals):5d} skipped(<256)={skipped}  " + "  ".join(parts))
    print(f"backward checked {sum(totals.values())} NAL(s) >=256 B -> "
          f"identical {totals['identical']}, needle-only {totals['needle-only']}, absent {totals['absent']}")
    print("Missing NALs can reflect incomplete capture; needle-only is not proof of identity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
