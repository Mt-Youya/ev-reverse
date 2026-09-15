"""What one function in the DLL touches: the functions it calls, the strings it names.

Written for the two decryption call sites that never fire. `0x34390` is called from four functions
in `0x310F0`..`0x379BC`, and those functions reference `json_params`, `wdisklist` and eight
encrypted strings; `0x1B480` sits in a ten-method table whose only construction path is
`0x385B0` -> `0x174C0`. Reading the neighbourhood is what turns a list of addresses into a
description of a flow.

    python flow_map.py <dll> 0x310f0 0x32067
    python flow_map.py <dll> 0x310f0 0x32067 0x174c0 0x195bc --strings
"""

import argparse
import struct
import sys

import capstone

from static_xref import Image


def wide_or_ascii(raw):
    """The DLL keeps most of its text as UTF-16, so an ASCII read of it returns one letter.

    The encrypted strings are stored end to end with no separator, so what comes back is the whole
    tail of the table rather than one entry; callers print a bounded slice of it.
    """
    if len(raw) >= 2 and raw[1] == 0:
        text = raw.decode("utf-16-le", "ignore").split("\x00")[0]
    else:
        text = raw.split(b"\x00")[0].decode("latin1", "replace")
    return text.encode("unicode_escape").decode("ascii")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("ranges", nargs="+", help="start end [start end ...] as hex RVAs")
    ap.add_argument("--strings", action="store_true", help="print the text each reference points at")
    args = ap.parse_args()

    img = Image(args.dll)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True

    pairs = [(int(args.ranges[i], 16), int(args.ranges[i + 1], 16))
             for i in range(0, len(args.ranges) - 1, 2)]

    for start, end in pairs:
        calls, refs = [], []
        data = img.read(start, end - start)
        offset = 0
        while offset < len(data):
            decoded = False
            for ins in md.disasm(data[offset:], img.base + start + offset):
                decoded = True
                rva = ins.address - img.base
                offset = rva - start + ins.size
                if rva > end:
                    break
                if ins.mnemonic in ("call", "jmp") and ins.operands:
                    target = ins.operands[0].imm if ins.operands[0].type == capstone.x86.X86_OP_IMM else None
                    if target:
                        calls.append((rva, target - img.base))
                for op in ins.operands:
                    if op.type != capstone.x86.X86_OP_MEM or op.mem.base != capstone.x86.X86_REG_RIP:
                        continue
                    target = ins.address + ins.size + op.mem.disp - img.base
                    refs.append((rva, target))
            if not decoded:
                offset += 1

        called = {}
        for rva, target in calls:
            where = img.containing(target)
            label = "0x{:x}..0x{:x}".format(where[0], where[1]) if where else "outside .pdata"
            called.setdefault(label, []).append(rva)
        print("=== 0x{:x}..0x{:x} ===".format(start, end))
        print("  {} call/jmp site(s) into {} distinct function(s)".format(len(calls), len(called)))
        for label, sites in sorted(called.items(), key=lambda kv: -len(kv[1])):
            print("    {:24s} x{:2d}  from {}".format(label, len(sites),
                                                      " ".join("0x{:x}".format(s) for s in sites[:4])))

        print("  {} rip-relative reference(s)".format(len(refs)))
        for rva, target in refs:
            where = img.containing(target)
            place = "0x{:x}..0x{:x}".format(where[0], where[1]) if where else "data"
            text = ""
            if args.strings:
                text = wide_or_ascii(img.read(target, 160))
            print("    at 0x{:x} -> 0x{:x} [{}] {}".format(rva, target, place, repr(text[:70]) if text else ""))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
