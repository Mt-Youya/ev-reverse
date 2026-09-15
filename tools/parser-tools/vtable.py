"""Resolve a C++ virtual call to the method it lands in.

`mov rax, [global] / mov rcx, [rax+0x78] / mov rax, [rcx] / call [rax+0xd0]` names no function.
This walks the chain statically: the export that produces the object, the global that caches it,
the vtable the global holds (read through the base relocation, since a vtable pointer in .data is
stored as an RVA plus a fixup), and then the slot.

    python vtable.py <dll> --instance ?instance@utils@bg@@YAPEAVAbstract_Utils@2@XZ
    python vtable.py <dll> --vt-from-global 0x2a1b0 --slots 8,26
"""

import argparse
import struct
import sys

from static_xref import Image

import annotate


def export_map(img):
    """Export name -> RVA. pefile reports export addresses as RVAs (imports it reports as VAs)."""
    out = {}
    for entry in getattr(img.pe, "DIRECTORY_ENTRY_EXPORT", []).symbols:
        if entry.name:
            out[entry.name.decode("ascii", "replace")] = entry.address
    return out


def reloc_value(img, rva):
    """The value a pointer-sized slot in the image holds once the loader has applied fixups."""
    raw = struct.unpack("<Q", img.read(rva, 8))[0]
    for block in getattr(img.pe, "DIRECTORY_ENTRY_BASERELOC", []):
        for entry in block.entries:
            if entry.rva == rva and entry.type == 3:  # IMAGE_REL_BASED_HIGHLOW / DIR64
                return img.base + raw
    return raw


def find_writes_to(img, target, limit=8):
    """Instructions of the form `mov [rip+disp], reg` whose destination is `target`."""
    hits = []
    for name, va, size in img.executable_ranges():
        data = img.read(va, size)
        for i in range(len(data) - 7):
            if data[i] != 0x48 or data[i + 1] != 0x89:
                continue
            modrm = data[i + 2]
            if modrm & 0xC7 not in (0x05,):  # [rip+disp32]
                continue
            disp = struct.unpack_from("<i", data, i + 3)[0]
            if va + i + 7 + disp == target:
                hits.append(va + i)
                if len(hits) >= limit:
                    return hits
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("--instance", help="export name that returns the object")
    ap.add_argument("--vt-from-global", type=lambda v: int(v, 0),
                    help="RVA of a global holding the object pointer")
    ap.add_argument("--vtable", type=lambda v: int(v, 0), help="RVA of the vtable itself")
    ap.add_argument("--slots", default="8,26", help="vtable byte offsets to resolve")
    ap.add_argument("--disasm", type=int, default=0, help="instructions to print per slot")
    args = ap.parse_args(argv)

    img = Image(args.dll)
    img.pe.parse_data_directories()
    slots = [int(s, 0) for s in args.slots.split(",")]

    vt_rva = None
    if args.instance:
        exports = export_map(img)
        rva = exports.get(args.instance)
        if rva is None:
            print(f"export not found; {len(exports)} exports, closest matches:")
            for name in exports:
                if "instance" in name and "utils" in name:
                    print("   ", name)
            return 1
        print(f"{args.instance} at {rva:#x}")
        data = img.read(rva, 0x60)
        for ins in annotate.md.disasm(data, img.base + rva):
            notes = []
            for op in ins.operands:
                if op.type == annotate.capstone.x86.X86_OP_MEM and \
                        op.mem.base == annotate.capstone.x86.X86_REG_RIP:
                    target = ins.address + ins.size + op.mem.disp - img.base
                    notes.append(f"{target:#x} = {reloc_value(img, target):#x}")
            print(f"  {ins.address - img.base:#08x}  {ins.mnemonic:<7} {ins.op_str}"
                  + ("   ; " + "; ".join(notes) if notes else ""))
    if args.vt_from_global:
        obj = reloc_value(img, args.vt_from_global)
        print(f"global {args.vt_from_global:#x} -> object {obj:#x}")
        vt_rva = obj - img.base
    if args.vtable:
        vt_rva = args.vtable

    if vt_rva is not None:
        for slot in slots:
            fn = img.read(vt_rva + slot, 8)
            if len(fn) < 8:
                print(f"  slot +{slot:#x}: unreadable")
                continue
            addr = struct.unpack("<Q", fn)[0]
            rva = addr - img.base
            hit = img.containing(rva)
            print(f"  slot +{slot:#x} -> {rva:#x} {hit}")
            if args.disasm:
                data = img.read(rva, args.disasm * 8)
                for ins in annotate.md.disasm(data, img.base + rva):
                    print(f"      {ins.address - img.base:#08x}  {ins.mnemonic:<7} {ins.op_str}")
                    args.disasm -= 1
                    if args.disasm <= 0:
                        break
    return 0


if __name__ == "__main__":
    sys.exit(main())
