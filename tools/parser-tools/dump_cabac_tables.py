"""Dump the CABAC tables the player's decoder actually uses, and line them up with stock FFmpeg's.

Ghidra put the player's CABAC tables at RVA 0x9691d0 / 0x9691f0 / 0x969258 / 0x969268 / 0x9692a0, each
entry four bytes wide (a 16-bit next state plus an 8-bit MPS-ish byte). Stock FFmpeg keeps its tables
in one much larger array starting at the `lps_range` bytes, and that array is nowhere in this file --
so the layout differs, and the question this answers is *how*: a permutation of the same numbers
(reproducible, and a decoder patch we could copy) or a different set of numbers entirely.

    python dump_cabac_tables.py <player dll> <stock ffmpeg>
"""

import struct
import sys


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


def dump(blob, offset, length, label):
    print(f"\n=== {label} (file offset 0x{offset:x}, {length} bytes)")
    for start in range(offset, offset + length, 16):
        chunk = blob[start:start + 16]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {start - offset:5d}  {chunk.hex(' '):<47}  {text}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    dll, sections = pe_sections(sys.argv[1])
    stock = open(sys.argv[2], "rb").read()

    print(f"{sys.argv[1]}: {len(dll):,} bytes")
    for name, virtual_address, virtual_size, raw_pointer, raw_size in sections:
        print(f"  section {name:<8} rva 0x{virtual_address:08x} vsize {virtual_size:>10,} "
              f"raw 0x{raw_pointer:08x} {raw_size:>10,}")

    for rva in (0x9691C0, 0x969250, 0x969290):
        offset = to_offset(sections, rva)
        if offset is not None:
            dump(dll, offset, 0x60, f"player tables at RVA 0x{rva:x}")

    lps = bytes([128, 176, 208, 240, 128, 167, 197, 227, 128, 158, 187, 216])
    at = stock.find(lps)
    if at != -1:
        dump(stock, at - 64, 0x140, "stock FFmpeg tables around lps_range")

    # do the player's tables reuse stock numbers at all?
    player_area = bytearray()
    for rva in range(0x969000, 0x969400, 0x100):
        offset = to_offset(sections, rva)
        if offset is not None:
            player_area += dll[offset:offset + 0x100]
    stock_table = stock[at - 64:at - 64 + 1024] if at != -1 else b""
    shared = {value: stock_table.count(value) for value in set(player_area)}
    overlap = sum(1 for value, count in shared.items() if count)
    print(f"\nplayer table area: {len(player_area)} bytes, {len(set(player_area))} distinct values")
    print(f"  values also present in the stock table block: {overlap} of {len(set(player_area))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
