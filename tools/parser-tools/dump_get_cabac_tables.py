"""Dump the three tables `get_cabac` actually indexes, and compare them with the H.264 spec's.

Ghidra's decompilation of the arithmetic decoder (`FUN_1803C4140`, FFmpeg's `get_cabac`) names its
tables outright:

    RangeLPS = DAT_1809023b0[state + (range & 0xC0) * 2]
    *state   = DAT_180902630[...]        (the state transition table)
    shift    = DAT_1809021b0[range]      (renormalisation)

so the question "was the entropy coder changed, or does this build just keep the table somewhere
else?" is answerable by reading those RVAs out of the file and laying them next to the spec's
rangeTabLPS. A relocated copy of the spec's numbers is a decoder we can reproduce; different numbers
are the protection itself.

    python dump_get_cabac_tables.py "D:\\Learning\\EVPlayer2\\PlayerLibRender56_vs.dll"
"""

import struct
import sys

# H.264 Table 9-44, one row per state, four columns (QP%4).
RANGE_TAB_LPS = [
    [128, 176, 208, 240], [128, 167, 197, 227], [128, 158, 187, 216], [123, 150, 178, 205],
    [116, 142, 169, 195], [111, 135, 160, 185], [105, 128, 152, 175], [100, 122, 144, 166],
    [95, 116, 137, 157], [90, 110, 130, 150], [85, 104, 123, 142], [81, 99, 117, 135],
    [77, 94, 111, 128], [73, 89, 105, 122], [69, 85, 100, 116], [66, 80, 95, 110],
    [62, 76, 90, 104], [59, 72, 86, 99], [56, 69, 81, 94], [53, 65, 77, 89],
    [51, 62, 73, 85], [48, 59, 69, 80], [46, 56, 66, 76], [43, 53, 63, 72],
    [41, 50, 59, 69], [39, 48, 56, 65], [37, 45, 54, 62], [35, 43, 51, 59],
]

TABLES = [
    ("renormalisation shift (DAT_1809021b0)", 0x9021B0, 512),
    ("LPS range       (DAT_1809023b0)", 0x9023B0, 512),
    ("state transition (DAT_180902630)", 0x902630, 512),
]


def pe_sections(path):
    blob = open(path, "rb").read()
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    count = struct.unpack_from("<H", blob, pe + 6)[0]
    optional_size = struct.unpack_from("<H", blob, pe + 20)[0]
    table = pe + 24 + optional_size
    sections = []
    for index in range(count):
        entry = table + index * 40
        name = blob[entry:entry + 8].rstrip(b"\x00").decode("ascii", "replace")
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from("<IIII", blob,
                                                                                  entry + 8)
        sections.append((name, virtual_address, virtual_size, raw_pointer, raw_size))
    return blob, sections


def to_offset(sections, rva):
    for name, virtual_address, virtual_size, raw_pointer, raw_size in sections:
        if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
            return raw_pointer + (rva - virtual_address)
    return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll"
    blob, sections = pe_sections(path)
    for name, rva, length in TABLES:
        offset = to_offset(sections, rva)
        print(f"\n=== {name}  rva 0x{rva:x}  file offset "
              f"{'0x%x' % offset if offset else 'unmapped'}")
        if offset is None:
            continue
        data = blob[offset:offset + length]
        for start in range(0, min(length, 256), 16):
            print(f"  {start:4d}  {data[start:start + 16].hex(' ')}")

    # The spec's table, flattened the two ways a compiler might store it.
    state_major = [value for row in RANGE_TAB_LPS for value in row]
    qp_major = [row[column] for column in range(4) for row in RANGE_TAB_LPS]
    offset = to_offset(sections, 0x9023B0)
    if offset is None:
        return 1
    data = blob[offset:offset + 512]
    for label, pattern in (("state-major (as the spec lists it)", bytes(state_major)),
                           ("qp-major (transposed)", bytes(qp_major))):
        where = data.find(pattern)
        print(f"\n  {label}: {'present at +%d' % where if where != -1 else 'ABSENT'}")
        if where == -1:
            head = pattern[:16]
            near = data.find(head[:4])
            print(f"    looking for {pattern[:16].hex(' ')} ...")
            if near != -1:
                print(f"    first four values found at +{near}: {data[near:near + 16].hex(' ')}")
                print(f"    expected                          : {pattern[:16].hex(' ')}")
    # value-level comparison: which spec values appear at all, and with what neighbours
    values = set(state_major)
    present = sorted(values & set(data))
    print(f"\n  spec values present somewhere in the table: {len(present)} of {len(values)}")
    missing = sorted(values - set(data))
    if missing:
        print(f"  spec values absent: {missing[:24]}{' ...' if len(missing) > 24 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
