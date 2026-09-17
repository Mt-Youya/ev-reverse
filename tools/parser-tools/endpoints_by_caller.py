"""Which endpoint each decryption call site serves, read off the strings it references.

`0x1EA10` has five callers and only three have ever fired. The two silent ones are not mysterious
if you read what they are near: each response handler builds its request from literals, and those
literals name the endpoint. So: find every `/student/...` string in the image, then find which
function references it, and report the mapping against the known call sites.

    python endpoints_by_caller.py
"""

import argparse
import re
import struct
import sys

import capstone

from static_xref import Image

CALLERS = {0x1B480: "call site 1", 0x26730: "call site 2 (segment list)",
           0x34390: "call site 3", 0x3B2B0: "call site 4 (descriptors)",
           0x42A30: "call site 5 (descriptors)"}

ENDPOINT = re.compile(rb"/student/[A-Za-z0-9_]+")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    args = ap.parse_args()

    img = Image(args.dll)
    blob = img.pe.__data__
    endpoints = {}
    for match in ENDPOINT.finditer(blob):
        text = match.group().decode()
        # file offset -> rva
        for name, va, vsize, raw, rawsize, _ in img.sections:
            if raw <= match.start() < raw + rawsize:
                endpoints.setdefault(text, []).append(va + (match.start() - raw))
                break
    print(f"{len(endpoints)} endpoint literal(s)")

    # references, scanned as RIP-relative operands
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    wanted = {rva: text for text, rvas in endpoints.items() for rva in rvas}
    hits = {}
    for name, va, size in img.executable_ranges():
        data = img.read(va, size)
        off = 0
        while off < len(data):
            decoded = False
            for ins in md.disasm(data[off:], img.base + va + off):
                decoded = True
                rva = ins.address - img.base
                off = rva - va + ins.size
                for op in ins.operands:
                    if op.type != capstone.x86.X86_OP_MEM or op.mem.base != capstone.x86.X86_REG_RIP:
                        continue
                    target = ins.address + ins.size + op.mem.disp - img.base
                    text = wanted.get(target)
                    if not text:
                        continue
                    fn = img.containing(rva)
                    key = (fn[0] if fn else rva, text)
                    hits[key] = hits.get(key, 0) + 1
            if not decoded:
                off += 1

    print("\nfunction -> endpoint it names")
    for (fn, text), count in sorted(hits.items()):
        marker = CALLERS.get(fn, "")
        print(f"  {fn:#08x}  {text:44s} x{count}  {marker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
