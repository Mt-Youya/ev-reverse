"""Check whether the decoder's H.264 tables are the stock FFmpeg ones, by fingerprinting the DLL.

A file whose slice headers parse, whose payload statistics match a working file's, and which both
FFmpeg generations fail from the first macroblock is not obviously encrypted. Another way to break
every stock decoder while keeping the bitstream looking ordinary is to decode it with *modified*
entropy tables: change the CABAC initialisation contexts, keep everything else, and only a decoder
carrying the same change can read it.

Those tables have known bytes, so the question is answerable with a substring search over the DLL
rather than by reversing the decoder. A missing fingerprint means that table was changed; a present
one is evidence (not proof) that this part of the decoder is stock.

    python find_tables.py "C:\\...\\PlayerLibRender56_vs.dll"
"""

import os
import sys

# Fingerprints from FFmpeg's h264_cabac.c and the H.264 spec tables it implements.
FINGERPRINTS = [
    ("lps_range rows 0-2 (spec table 9-44)", bytes([
        128, 176, 208, 240,
        128, 167, 197, 227,
        128, 158, 187, 216])),
    ("lps_range rows 3-5", bytes([
        123, 150, 178, 205,
        116, 142, 169, 195,
        111, 135, 160, 185])),
    ("lps_range rows 6-8", bytes([
        105, 128, 152, 175,
        100, 122, 144, 166,
        95, 116, 137, 157])),
    ("renorm_table_32", bytes([
        6, 5, 4, 4, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2,
        1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0])),
    # cabac_context_init_I opens with 20, -15, 2, 54, 3, 74 as int16.
    ("cabac_context_init_I[0..5]", (20).to_bytes(2, "little", signed=True)
     + (-15).to_bytes(2, "little", signed=True)
     + (2).to_bytes(2, "little", signed=True)
     + (54).to_bytes(2, "little", signed=True)
     + (3).to_bytes(2, "little", signed=True)
     + (74).to_bytes(2, "little", signed=True)),
    # cabac_context_init_PB[0] opens with 20, -15, 2, 54, 3, 74 as well; [1] and [2] start at 0.
    ("ff_h264_chroma_qp", bytes([
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 29, 30])),
    ("zigzag_8x8 (dct coef order)", bytes([
        0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5])),
    # cabac_context_init_I/PB are int8 pairs, one pair per context, not int16 -- which is why the
    # int16 form of this fingerprint was never going to be found anywhere, stock binaries included.
    ("cabac_context_init pairs 0..5", bytes([
        20 & 0xFF, (-15) & 0xFF, 2, 54, 3, 74])),
    ("cabac_context_init pairs 0..12", bytes([
        20 & 0xFF, (-15) & 0xFF, 2, 54, 3, 74, (-28) & 0xFF, 127,
        (-23) & 0xFF, 104, (-6) & 0xFF, 53, (-9) & 0xFF, 16])),
]

# The spec's rangeTabLPS, one row at a time. Searching rows separately distinguishes "the table is
# stored in another arrangement" from "these numbers are not in this binary at all": the first shows
# up as rows that are present but never adjacent, the second as rows that are simply missing.
LPS_ROWS = [
    (0, [128, 176, 208, 240]), (1, [128, 167, 197, 227]), (2, [128, 158, 187, 216]),
    (3, [123, 150, 178, 205]), (4, [116, 142, 169, 195]), (5, [111, 135, 160, 185]),
    (6, [105, 128, 152, 175]), (7, [100, 122, 144, 166]), (8, [95, 116, 137, 157]),
]
for _index, _row in LPS_ROWS:
    FINGERPRINTS.append((f"lps_range row {_index} alone", bytes(_row)))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    blob = open(path, "rb").read()
    print(f"{os.path.basename(path)}  {len(blob):,} bytes\n")
    for name, pattern in FINGERPRINTS:
        hits = []
        start = blob.find(pattern)
        while start != -1 and len(hits) < 4:
            hits.append(start)
            start = blob.find(pattern, start + 1)
        where = ", ".join(f"0x{offset:x}" for offset in hits) if hits else "-"
        verdict = "found" if hits else "NOT FOUND"
        print(f"  {verdict:<9} {name:<38} at {where}")
    print("\nA table FFmpeg certainly contains but this DLL does not is a table that was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
