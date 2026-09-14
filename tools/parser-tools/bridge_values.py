"""Every place the player asks the bridge for a runtime value, with the name it asks for.

`0x42660` builds a segment key as `MD5hex(tk + filename + extra)`, where `extra` comes from a
bridge lookup whose name is an obfuscated constant held in a string pool. The same lookup pattern
appears elsewhere in the DLL with different constants and a different length argument, so listing
all of them shows what the mechanism is for — and what kind of value the key path is asking for.

    python bridge_values.py <dll>
"""

import argparse
import sys

import capstone

from static_xref import Image

import annotate

GLOBAL = 0xBF26C8  # the cached pointer whose +0x78 object carries the lookup methods


def utf16_at(img, rva, limit=128):
    """The UTF-16 string at an RVA, which is how Qt passes a name to these lookups."""
    raw = img.read(rva, limit)
    out = []
    for i in range(0, len(raw) - 1, 2):
        unit = raw[i] | (raw[i + 1] << 8)
        if unit == 0:
            break
        if unit < 0x20 or unit > 0x7E:
            return None
        out.append(chr(unit))
    return "".join(out) if out else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("--window", type=int, default=40, help="instructions to scan after a reference")
    args = ap.parse_args(argv)

    img = Image(args.dll)
    md = annotate.md
    rows = []
    for name, va, size in img.executable_ranges():
        data = img.read(va, size)
        off = 0
        while off < len(data):
            decoded = False
            for ins in md.disasm(data[off:], img.base + va + off):
                decoded = True
                rva = ins.address - img.base
                off = rva - va + ins.size
                if not any(op.type == capstone.x86.X86_OP_MEM
                           and op.mem.base == capstone.x86.X86_REG_RIP
                           and ins.address + ins.size + op.mem.disp - img.base == GLOBAL
                           for op in ins.operands):
                    continue
                # walk forward from here looking for the vtable call
                window = img.read(rva, 0x300)
                length = None
                string = None
                for probe in md.disasm(window, img.base + rva):
                    if probe.mnemonic == "mov" and probe.op_str.startswith("r9d, "):
                        length = probe.op_str.split(", ")[1]
                    if probe.mnemonic == "lea" and probe.op_str.startswith("r8, [rip"):
                        target = probe.address + probe.size + probe.operands[1].mem.disp - img.base
                        string = utf16_at(img, target) or annotate.cstring(img, target, 96)
                    if probe.mnemonic == "call" and "0xd0]" in probe.op_str:
                        rows.append((rva, length, string))
                        break
                if len(rows) > 40:
                    break
            if not decoded:
                off += 1

    seen = set()
    print(f"{len(rows)} bridge lookup(s)")
    for rva, length, string in rows:
        signature = (length, string)
        if signature in seen:
            continue
        seen.add(signature)
        fn = img.containing(rva)
        where = f"fn {fn[0]:#x}" if fn else "?"
        print(f"  {rva:#08x} {where:<14} len/id={str(length):<10} name={string!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
