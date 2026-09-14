"""Find the function that contains an RVA, and every direct call site that reaches it.

Static route to questions the live player was being asked with breakpoints: `.pdata` gives the
real function boundaries (the `cc cc cc` scan does not), and the callers of a known entry point
are one pass over the executable sections.

    python static_xref.py <dll> containing 0x20ec0 [--disasm 40]
    python static_xref.py <dll> callers 0x20ec0 [--disasm 60]
    python static_xref.py <dll> section 0x20ec0

`--disasm N` prints N instructions from the function entry (containing) or from the call site
(callers). RVAs are file RVAs, not file offsets.
"""

import argparse
import bisect
import struct
import sys

import capstone
import pefile

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = False


class Image:
    def __init__(self, path):
        self.pe = pefile.PE(path, fast_load=True)
        self.path = path
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.sections = []
        for s in self.pe.sections:
            name = s.Name.rstrip(b"\0").decode("ascii", "replace")
            self.sections.append((name, s.VirtualAddress, s.Misc_VirtualSize,
                                  s.PointerToRawData, s.SizeOfRawData,
                                  s.Characteristics))
        self.pdata = self._load_pdata()
        self.starts = [e[0] for e in self.pdata]

    def _load_pdata(self):
        for name, va, vsize, raw, rawsize, _ in self.sections:
            if name != ".pdata":
                continue
            data = self.pe.__data__[raw:raw + rawsize]
            out = []
            for off in range(0, len(data) - 11, 12):
                begin, end, unwind = struct.unpack_from("<III", data, off)
                if begin == 0 or end == 0:
                    continue
                out.append((begin, end, unwind))
            out.sort()
            return out
        return []

    def read(self, rva, size):
        """Bytes at an RVA. A section's virtual size can exceed its raw size, and the tail the
        loader zero-fills is *not* in the file: reading past `SizeOfRawData` would return whatever
        bytes happen to follow, which reads as data and is how a null pointer becomes a plausible
        address."""
        for name, va, vsize, raw, rawsize, _ in self.sections:
            if va <= rva < va + max(vsize, rawsize):
                offset = rva - va
                if offset >= rawsize:
                    return b"\0" * size
                available = min(size, rawsize - offset)
                data = self.pe.__data__[raw + offset:raw + offset + available]
                return data + b"\0" * (size - len(data))
        return b""

    def section_of(self, rva):
        for name, va, vsize, raw, rawsize, chars in self.sections:
            if va <= rva < va + max(vsize, rawsize):
                return name, va, vsize, raw, chars
        return None

    def containing(self, rva):
        """The .pdata entry whose range covers rva, or None."""
        i = bisect.bisect_right(self.starts, rva) - 1
        if i >= 0:
            begin, end, unwind = self.pdata[i]
            if begin <= rva < end:
                return begin, end, unwind
        return None

    def executable_ranges(self):
        for name, va, vsize, raw, rawsize, chars in self.sections:
            if chars & 0x20000000:  # IMAGE_SCN_MEM_EXECUTE
                yield name, va, min(vsize, rawsize)


def find_calls(img, target):
    """Every `call rel32` / `jmp rel32` whose destination is target."""
    hits = []
    for name, va, size in img.executable_ranges():
        data = img.read(va, size)
        for i in range(len(data) - 4):
            op = data[i]
            if op not in (0xE8, 0xE9):
                continue
            rel = struct.unpack_from("<i", data, i + 1)[0]
            dest = va + i + 5 + rel
            if dest == target:
                hits.append((va + i, name, "call" if op == 0xE8 else "jmp"))
    return hits


def show(img, rva, count):
    data = img.read(rva, count * 8)
    for ins in md.disasm(data, img.base + rva):
        print(f"    {ins.address - img.base:#08x}  {ins.mnemonic:<8} {ins.op_str}")
        count -= 1
        if count <= 0:
            return


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("mode", choices=["containing", "callers", "section"])
    ap.add_argument("rva")
    ap.add_argument("--disasm", type=int, default=0)
    ap.add_argument("--before", type=lambda v: int(v, 0), default=0x40,
                    help="bytes of context printed before a call site")
    args = ap.parse_args(argv)

    img = Image(args.dll)
    rva = int(args.rva, 0)
    print(f"{args.dll}: image base {img.base:#x}, {len(img.pdata)} .pdata entries")

    if args.mode == "section":
        print(img.section_of(rva))
        return 0

    if args.mode == "containing":
        hit = img.containing(rva)
        if not hit:
            print(f"no .pdata range covers {rva:#x}")
            return 1
        begin, end, unwind = hit
        print(f"{rva:#x} is in function {begin:#x}..{end:#x} "
              f"({end - begin} bytes), unwind {unwind:#x}, offset +{rva - begin:#x}")
        if args.disasm:
            show(img, begin, args.disasm)
        return 0

    # callers: resolve the containing function first, then look for calls to its entry
    entry = rva
    hit = img.containing(rva)
    if hit and hit[0] != rva:
        print(f"note: {rva:#x} is inside {hit[0]:#x}; searching calls to the entry {hit[0]:#x}")
        entry = hit[0]
    hits = find_calls(img, entry)
    print(f"{len(hits)} direct reference(s) to {entry:#x}")
    for addr, sec, kind in hits:
        caller = img.containing(addr)
        where = f"in {caller[0]:#x}..{caller[1]:#x}" if caller else "outside .pdata"
        print(f"  {kind} at {addr:#x} ({sec}) {where}")
    if args.disasm:
        for addr, _, _ in hits:
            print(f"\n  around {addr:#x}:")
            start = max(addr - args.before, 0)
            data = img.read(start, args.before + 0x20)
            for ins in md.disasm(data, img.base + start):
                mark = "  <== call" if ins.address - img.base == addr else ""
                print(f"    {ins.address - img.base:#08x}  {ins.mnemonic:<8} {ins.op_str}{mark}")
                if ins.address - img.base > addr:
                    break
    return 0


if __name__ == "__main__":
    sys.exit(main())
