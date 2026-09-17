"""Every `bg::regist_value_type(id, name, getter, setter)` call the player makes.

The bridge exposes values to its modules; a module registers a type id, a name, and the two
functions that read and write it. The id is what a lookup passes — so the id used by the segment
key path (0xa6) names the provider that produces the third MD5 input, and the getter registered
under that id is where the value is actually computed.

    python registrations.py <dll>
"""

import argparse
import struct
import sys

import capstone

from static_xref import Image

import annotate

REGIST = "?regist_value_type@bg@@YAXHPEBDP6A"

QT_IDS = {hex(v) for v in range(0x1F, 0x29)}  # qstr/point/rect/size/color/font, per-thread


def utf16_at(img, rva, limit=128):
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
    ap.add_argument("--id", type=lambda v: int(v, 0), help="only the registration with this id")
    args = ap.parse_args(argv)

    img = Image(args.dll)
    img.pe.parse_data_directories()
    slot = None
    for entry in getattr(img.pe, "DIRECTORY_ENTRY_IMPORT", []):
        for imp in entry.imports:
            if imp.name and REGIST.encode() in imp.name:
                slot = imp.address - img.base
    if slot is None:
        print("no regist_value_type import")
        return 1
    print(f"regist_value_type import slot {slot:#x}")

    # find every `call qword ptr [rip + disp]` that lands on the slot
    calls = []
    for name, va, size in img.executable_ranges():
        data = img.read(va, size)
        for i in range(len(data) - 6):
            if data[i] != 0xFF or data[i + 1] != 0x15:
                continue
            disp = struct.unpack_from("<i", data, i + 2)[0]
            if va + i + 6 + disp == slot:
                calls.append(va + i)
    print(f"{len(calls)} call site(s)")

    rows = []
    for call in calls:
        # Decode from the function's own entry, not from call-0x40: starting mid-instruction
        # desyncs the sweep and silently loses the arguments of the first call in each group.
        fn = img.containing(call)
        start = fn[0] if fn and call - fn[0] < 0x400 else max(call - 0x40, 0)
        window = img.read(start, call - start)
        args_found = {}
        for ins in annotate.md.disasm(window, img.base + start):
            if ins.mnemonic == "mov" and ins.op_str.startswith("ecx, "):
                args_found["id"] = ins.op_str.split(", ")[1]
                args_found["id_at"] = ins.address - img.base
            if ins.mnemonic == "lea" and ins.op_str.startswith("rdx, [rip"):
                target = ins.address + ins.size + ins.operands[1].mem.disp - img.base
                # names registered by this player live in a UTF-16 pool, not in ASCII strings
                text = utf16_at(img, target, 64) or annotate.cstring(img, target, 64)
                if text:
                    args_found["name"] = text
            if ins.mnemonic == "lea" and ins.op_str.startswith("r8, [rip"):
                args_found["getter"] = ins.address + ins.size + ins.operands[1].mem.disp - img.base
            if ins.mnemonic == "lea" and ins.op_str.startswith("r9, [rip"):
                args_found["setter"] = ins.address + ins.size + ins.operands[1].mem.disp - img.base
        rows.append((call, args_found))

    for call, found in rows:
        if args.id is not None and found.get("id") != hex(args.id):
            continue
        line = (f"  at {call:#08x}  id={found.get('id', '?'):<8} name={found.get('name')!r:<16} "
                f"getter={found.get('getter', 0):#08x} setter={found.get('setter', 0):#08x}")
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
