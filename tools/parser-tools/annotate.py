"""Disassemble an RVA range with the operands resolved: imports named, strings shown, calls
labelled with the .pdata function they land in.

The plain disassembler in `static_xref.py` answers "what does it say"; this one answers "what
does it touch", which is the question when reading a derivation off the code. Every RIP-relative
operand is followed to its target and printed as an imported symbol, a C string, or hex.

    python annotate.py <dll> 0x42660 0x42a21
    python annotate.py <dll> 0x42660 0x42a21 --string 0x80db50
"""

import argparse
import struct
import sys

import capstone

from static_xref import Image

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = True


def imports(img):
    """IAT slot RVA -> "dll!name", plus delay-load entries when present."""
    out = {}
    pe = img.pe
    pe.parse_data_directories(directories=[
        pefile_dir(pe, "IMAGE_DIRECTORY_ENTRY_IMPORT"),
        pefile_dir(pe, "IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"),
    ])
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        dll = entry.dll.decode("ascii", "replace")
        for imp in entry.imports:
            if imp.name:
                out[imp.address - img.base] = f"{dll}!{imp.name.decode('ascii', 'replace')}"
            else:
                out[imp.address - img.base] = f"{dll}!#{imp.ordinal}"
    for entry in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []):
        dll = entry.dll.decode("ascii", "replace")
        for imp in entry.imports:
            if imp.name:
                out[imp.address - img.base] = f"{dll}!{imp.name.decode('ascii', 'replace')}"
    return out


def pefile_dir(pe, name):
    import pefile
    return pefile.DIRECTORY_ENTRY[name]


def cstring(img, rva, limit=96):
    data = img.read(rva, limit)
    end = data.find(b"\0")
    if end < 0:
        return None
    raw = data[:end]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or not all(0x20 <= ord(c) < 0x7f or c in "\t" for c in text):
        return None
    return text


def describe_target(img, iat, rva, depth=0):
    if rva in iat:
        return f"-> {iat[rva]}"
    hit = img.containing(rva)
    where = f"fn {hit[0]:#x}..{hit[1]:#x}" if hit else "no .pdata"
    text = cstring(img, rva)
    if text is not None:
        return f"-> str {text!r} [{where}]"
    qword = img.read(rva, 8)
    if len(qword) == 8 and depth == 0:
        value = struct.unpack("<Q", qword)[0]
        return f"-> [{where}] = {value:#x}"
    return f"-> data [{where}]"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--string", type=lambda v: int(v, 0), action="append", default=[])
    args = ap.parse_args(argv)

    img = Image(args.dll)
    iat = imports(img)
    start, end = int(args.start, 0), int(args.end, 0)
    data = img.read(start, end - start)
    print(f"{len(iat)} import slot(s); disassembling {start:#x}..{end:#x}")
    for ins in md.disasm(data, img.base + start):
        rva = ins.address - img.base
        notes = []
        for op in ins.operands:
            if op.type == capstone.x86.X86_OP_MEM and op.mem.base == capstone.x86.X86_REG_RIP:
                # capstone reports the displacement only; resolve it against the next instruction
                target = ins.address + ins.size + op.mem.disp - img.base
                notes.append(f"mem {target:#x} {describe_target(img, iat, target)}")
            elif op.type in (capstone.x86.X86_OP_IMM,) and ins.mnemonic in ("call", "jmp"):
                target = op.imm - img.base
                if img.read(target, 1):
                    notes.append(describe_target(img, iat, target))
        suffix = ("   ; " + "; ".join(notes)) if notes else ""
        print(f"{rva:#08x}  {ins.mnemonic:<8} {ins.op_str}{suffix}")

    for rva in args.string:
        data = img.read(rva, 128)
        print(f"\n{rva:#x}: {data[:64].hex(' ')}")
        print(f"   as text: {cstring(img, rva, 128)!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
