"""Check, byte for byte, whether the player's decoder input is what we decrypted.

`captured/pairing/` holds two sides of the same moment: the payload of every packet the player
handed to `h264_decode_frame` (`*.bin`), and the elementary stream we extracted from the segment
each key named (`*.h264`). An earlier pass compared these by aligning NALs by length, which is a
heuristic -- equal-length NALs can be aligned while their bytes differ. This does the exact test
instead, in both directions:

  forward  every captured packet: is the whole packet present verbatim in one of our streams?
  backward every NAL of every stream: is it present verbatim in some captured packet?

The backward direction is the one that matters when packets are missing (a segment whose key was set
before the capture began contributes none), because it answers per NAL rather than per packet: if a
stream's small NALs all appear and its large ones never do, the transform lives in the large ones.

    python verify_packet_identity.py
"""

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRING = os.path.join(HERE, "captured", "pairing")


def annex_b_nals(es):
    starts = []
    i = 0
    while i < len(es) - 3:
        if es[i] == 0 and es[i + 1] == 0 and es[i + 2] == 1:
            starts.append(i + 3)
            i += 3
        else:
            i += 1
    out = []
    for index, body in enumerate(starts):
        end = starts[index + 1] - 3 if index + 1 < len(starts) else len(es)
        if end > body:
            out.append((es[body] & 0x1F, body, end - body))
    return out


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
    streams = {}
    for name in sorted(os.listdir(PAIRING)):
        if name.endswith(".h264"):
            streams[name] = open(os.path.join(PAIRING, name), "rb").read()
    if not streams:
        print("no .h264 files in captured/pairing -- run pair_playback.py first")
        return 1
    total = sum(len(v) for v in streams.values())
    print(f"{len(streams)} stream(s), {total:,} bytes total\n")

    bins = sorted(n for n in os.listdir(PAIRING) if n.endswith(".bin"))
    blobs = {name: open(os.path.join(PAIRING, name), "rb").read() for name in bins}
    haystack = b"\x00\x00\x00\x01".join(blobs.values())
    print(f"{len(blobs)} captured packet(s), {len(haystack):,} bytes of them\n")

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
    for name, stream in streams.items():
        nals = annex_b_nals(stream)
        covered = collections.defaultdict(lambda: [0, 0])
        for ntype, offset, size in nals:
            if size < 96:
                covered[bucket(size)][0] += 1
                continue
            needle = stream[offset + size // 2:offset + size // 2 + 64]
            covered[bucket(size)][0 if needle in haystack else 1] += 1
        parts = []
        for key in ("0-255", "256-1k", "1k-4k", "4k-16k", "16k-64k", "64k+"):
            if key in covered:
                same, other = covered[key]
                parts.append(f"{key}:{same}/{same + other}")
        print(f"  {name[:52]:<52} nals={len(nals):5d}  " + "  ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
