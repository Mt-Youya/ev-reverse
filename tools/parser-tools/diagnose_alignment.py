"""Work out why some decrypted segments cannot be read, by looking at the bytes rather than guessing.

Several hundred cached files decrypt into something ffprobe refuses. The clue is arithmetic: the
encrypted file's length is not a multiple of the 188-byte packet size (372,432 = 188 x 1981 + 4), and
the project's decryptor only strips `#` padding of up to fifteen bytes, so four bytes of *something*
survive into the plaintext and the stream never lines up.

This measures it instead of assuming: for each file it reports the length modulo 188, the sync-byte
ratio at each of the 188 possible alignments, and what the ratio becomes once the surplus bytes are
dropped from the end or the start. The alignment with a 100% sync ratio is the answer, and whether it
is at the start or the end says which end carries the surplus.

    python diagnose_alignment.py [--sample 12] [--lesson 119354]
"""

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r"D:\Downloads\EVPlayer2Downloads"
LIBRARY = os.path.join(HERE, "captured", "keys_merged.json")


def sync_ratio(blob, stride=188, limit=400):
    usable = min(len(blob) // stride, limit)
    if usable == 0:
        return 0.0, 0
    good = sum(1 for index in range(usable) if blob[index * stride] == 0x47)
    return good / usable, usable


def alignment_profile(blob, stride=188):
    """Best offset to start counting packets from, ignoring the tail."""
    best = (0.0, 0)
    for offset in range(stride):
        ratio, usable = sync_ratio(blob[offset:], stride)
        if ratio > best[0]:
            best = (ratio, offset)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="119354")
    ap.add_argument("--sample", type=int, default=12)
    ap.add_argument("--library", default=LIBRARY)
    args = ap.parse_args()

    sys.path.insert(0, HERE)
    from build_videos import decrypt

    library = json.load(open(args.library, encoding="utf-8"))
    names = [n for n in sorted(library) if n.startswith(args.lesson)
             and os.path.exists(os.path.join(CACHE_DIR, n))]

    verdicts = collections.Counter()
    shown = 0
    for name in names:
        raw = open(os.path.join(CACHE_DIR, name), "rb").read()
        plain = decrypt(raw, library[name], name)
        surplus = len(plain) % 188
        ratio, usable = sync_ratio(plain)
        tail_ratio, tail_offset = alignment_profile(plain[:-surplus] if surplus else plain)
        head_ratio, head_offset = alignment_profile(plain[surplus:] if surplus else plain)
        if ratio > 0.99:
            verdicts["already aligned"] += 1
            continue
        if tail_ratio > 0.99:
            verdicts["surplus at the end"] += 1
        elif head_ratio > 0.99:
            verdicts["surplus at the start"] += 1
        else:
            verdicts["unexplained"] += 1
        if shown < args.sample:
            shown += 1
            print(f"\n{name}  encrypted {len(raw):,}  plain {len(plain):,}  surplus {surplus}")
            print(f"  ciphertext % 16 = {len(raw) % 16}   plaintext % 188 = {surplus}")
            print(f"  sync as-is          : {ratio * 100:6.1f}% of {usable} packets")
            print(f"  after dropping tail : {tail_ratio * 100:6.1f}% at offset {tail_offset}")
            print(f"  after dropping head : {head_ratio * 100:6.1f}% at offset {head_offset}")
            print(f"  head bytes: {plain[:16].hex(' ')}")
            print(f"  tail bytes: {plain[-16:].hex(' ')}")

    print(f"\n{len(names)} file(s) examined")
    for verdict, count in verdicts.most_common():
        print(f"  {count:5d}  {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
