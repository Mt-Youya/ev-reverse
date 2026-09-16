"""Test whether the slice payload has been XORed, by looking at byte statistics by position.

If a payload is real CABAC data, its bytes are strongly skewed: a lot of small values, a lot of
zeros, entropy well under 8 bits. XORing every byte with a pseudo-random keystream makes the result
uniform, which is visible in the byte histogram without knowing the key. A sparse scrambler -- every
Nth byte -- leaves the other positions skewed and shows up as a difference *between* position classes
rather than as flat entropy everywhere.

So: split the payload by index modulo K, and report per class the zero-byte share and the entropy.
Real data has classes that all look alike and skewed; encrypted data has classes that all look alike
and flat; a sparse scrambler has classes that disagree with each other.

    python payload_stats.py <grey.ts> <good.ts> [...] [--mod 16]
"""

import argparse
import collections
import math
import os
import sys

from slice_probe import nals_of, parse_pps, parse_sps, parse_slice_header, unescape


def entropy(counts, total):
    return -sum((c / total) * math.log2(c / total) for c in counts.values() if c)


def class_stats(payload, mod):
    classes = collections.defaultdict(collections.Counter)
    for index, byte in enumerate(payload):
        classes[index % mod][byte] += 1
    out = []
    for key in sorted(classes):
        counts = classes[key]
        total = sum(counts.values())
        out.append((key, total, counts[0] / total, entropy(counts, total)))
    return out


def payloads(path, want):
    blob = open(path, "rb").read()
    nals = nals_of(blob)
    sps = pps = None
    for nal_type, body in nals:
        rbsp, _ = unescape(body[1:])
        if nal_type == 7:
            sps = parse_sps(rbsp)
        elif nal_type == 8:
            pps = parse_pps(rbsp)
    out = []
    for nal_type, body in nals:
        if nal_type not in (1, 5):
            continue
        rbsp, _ = unescape(body[1:])
        header = parse_slice_header(rbsp, sps, pps, nal_type)
        payload = rbsp[header["header_bytes"]:]
        out.append((nal_type, payload))
    out.sort(key=lambda item: -len(item[1]))
    return out[:want]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--mod", type=int, default=16)
    ap.add_argument("--want", type=int, default=2)
    args = ap.parse_args()

    for path in args.files:
        print(f"\n=== {os.path.basename(path)}")
        for nal_type, payload in payloads(path, args.want):
            name = "IDR" if nal_type == 5 else "non-IDR"
            overall = collections.Counter(payload)
            zeros = overall[0] / len(payload)
            print(f"  {name} payload {len(payload):,} B  zeros={zeros * 100:5.1f}%  "
                  f"entropy={entropy(overall, len(payload)):.3f} bits")
            rows = class_stats(payload, args.mod)
            flat = "  ".join(f"{key}:{zero * 100:4.1f}%/{ent:4.2f}" for key, _, zero, ent in rows)
            print(f"    by position mod {args.mod} (zeros%/entropy): {flat}")
            spreads = [ent for _, _, _, ent in rows]
            print(f"    entropy spread across classes: {max(spreads) - min(spreads):.3f} bits "
                  f"({min(spreads):.3f} .. {max(spreads):.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
