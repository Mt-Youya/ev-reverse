"""Compare each traced copy against the packet bytes it was copied from.

`trace_copies.py` names its output `copy_<pts>_off<source offset>_<size>.bin`, and writes the packet
it came from as `packet_<pts>_<size>.bin`, so the two can be lined up exactly. Identical bytes mean the
copy is a relocation -- the decoder really does read our bytes, and the second layer is further down.
A structured difference (a repeating XOR, a prefix that matches and then diverges, a length that does
not add up) is the layer itself, with the plaintext on disk.

    python analyze_copies.py captured/copies
"""

import os
import re
import sys

NAME = re.compile(r"copy_(\d+)_off(\d+)_(\d+)(?:_at([0-9a-f]+))?\.bin$")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    root = sys.argv[1]
    packets = {}
    for name in os.listdir(root):
        match = re.match(r"packet_(\d+)_(\d+)\.bin$", name)
        if match:
            packets[int(match.group(1))] = os.path.join(root, name)

    rows = []
    for name in sorted(os.listdir(root)):
        match = NAME.match(name)
        if not match:
            continue
        pts, offset, size = (int(match.group(i)) for i in (1, 2, 3))
        path = os.path.join(root, name)
        blob = open(path, "rb").read()
        packet_path = packets.get(pts)
        if not packet_path:
            rows.append((name, "no packet recorded", "", ""))
            continue
        packet = open(packet_path, "rb").read()
        window = packet[offset:offset + size]
        if len(window) != size:
            rows.append((name, f"window short ({len(window)} of {size})", "", ""))
            continue
        if window == blob:
            rows.append((name, f"identical ({size:,} B)", "identical", "0"))
            continue
        first = next(i for i in range(size) if window[i] != blob[i])
        diff = sum(1 for i in range(size) if window[i] != blob[i])
        xors = " ".join(f"{window[i] ^ blob[i]:02x}" for i in range(first, min(first + 16, size)))
        rows.append((name, f"DIFFERENT ({diff:,} of {size:,} bytes)", f"first at +{first}",
                     f"xor: {xors}"))

    for name, verdict, where, detail in rows:
        print(f"{name:<48} {verdict:<34} {where:<16} {detail}")
    print(f"\n{len(rows)} copy/copies compared")
    different = [row for row in rows if row[1].startswith("DIFFERENT")]
    print("RESULT: " + ("every copy is a plain relocation of our bytes"
                        if not different else
                        f"{len(different)} copy/copies are NOT our bytes -- that is the layer"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
