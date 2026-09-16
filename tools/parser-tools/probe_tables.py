"""Read the CABAC table neighbourhood out of a stock FFmpeg and look for it in the player's DLL.

The player's decoder is FFmpeg 4.2.x by its own assertion strings, and the stock 4.2.2 build on this
machine contains the H.264 `lps_range` table at a known offset. That makes the table's *surroundings*
usable as a fingerprint: if the DLL contains the bytes around the table but not the table, the table
itself is what was changed -- which would be a decoder patch, not a cipher, and one that could be
copied back out.

    python probe_tables.py <stock binary> <player dll>
"""

import os
import sys

LPS = bytes([128, 176, 208, 240, 128, 167, 197, 227, 128, 158, 187, 216])


def windows(blob, offset, before=64, after=384, step=32, width=32):
    start = max(0, offset - before)
    end = min(len(blob), offset + after)
    out = []
    for position in range(start, end - width, step):
        out.append((position - offset, blob[position:position + width]))
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    stock = open(sys.argv[1], "rb").read()
    dll = open(sys.argv[2], "rb").read()

    hits = []
    start = stock.find(LPS)
    while start != -1 and len(hits) < 6:
        hits.append(start)
        start = stock.find(LPS, start + 1)
    print(f"{len(hits)} lps_range table(s) in {os.path.basename(sys.argv[1])}: "
          f"{[hex(h) for h in hits]}\n")
    if not hits:
        return 1

    print("bytes around the first table (offset relative to the table start):")
    base = hits[0]
    for relative in range(-64, 64, 16):
        chunk = stock[base + relative:base + relative + 16]
        print(f"  {relative:+5d}  {chunk.hex(' ')}")

    print("\nwhich 32-byte windows around it also appear in the player's DLL?")
    found = missing = 0
    for relative, window in windows(stock, base):
        where = dll.find(window)
        if where == -1:
            missing += 1
            mark = "absent"
        else:
            found += 1
            mark = f"present at 0x{where:x}"
        if abs(relative) <= 96 or mark == "absent":
            print(f"  {relative:+5d}  {window[:24].hex(' ')}  {mark}")
    print(f"\n{found} window(s) present, {missing} absent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
