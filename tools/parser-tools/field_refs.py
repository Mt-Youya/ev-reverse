"""Every instruction that touches `[reg + disp]` for one displacement, across the executable
sections — or every instruction whose RIP-relative operand resolves to one address.

The question "where does field +0x288 get written" is a field-offset question, and a linear
sweep over .text answers it without a live process. `--rip-target` asks the sibling question,
"who reads this global", which is how a lazily-initialised singleton is found. Desync is the
risk of a linear sweep; the sweep is therefore reported with the mnemonic so a garbled stretch
is visible rather than silently believed, and `--writes-only` filters to stores.

    python field_refs.py <dll> 0x288 [--writes-only] [--max 200]
    python field_refs.py <dll> --rip-target 0xbf26c8
"""

import argparse
import sys

import capstone

from static_xref import Image

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = True

WRITE_MNEMONICS = {
    "mov", "movups", "movaps", "movdqu", "movdqa", "movzx", "movsx", "movsxd",
    "lea",  # not a store, but the address is usually the store's operand
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("disp", nargs="?", help="field displacement, e.g. 0x288")
    ap.add_argument("--rip-target", type=lambda v: int(v, 0),
                    help="report instructions whose RIP-relative operand is this RVA")
    ap.add_argument("--rip-range", help="report RIP-relative operands inside START:END (hex)")
    ap.add_argument("--writes-only", action="store_true")
    ap.add_argument("--max", type=int, default=400)
    ap.add_argument("--context", type=lambda v: int(v, 0), default=0,
                    help="also print this many bytes of disassembly after each hit")
    args = ap.parse_args(argv)

    img = Image(args.dll)
    disp = int(args.disp, 0) if args.disp else None
    rip_target = args.rip_target
    rip_range = None
    if args.rip_range:
        lo, _, hi = args.rip_range.partition(":")
        rip_range = (int(lo, 16), int(hi, 16))
    if disp is None and rip_target is None and rip_range is None:
        ap.error("give a displacement, --rip-target or --rip-range")

    def matches(ins):
        for op in ins.operands:
            if op.type != capstone.x86.X86_OP_MEM:
                continue
            if disp is not None and op.mem.disp == disp and op.mem.base != capstone.x86.X86_REG_RIP:
                return True
            if (rip_target is not None or rip_range is not None) \
                    and op.mem.base == capstone.x86.X86_REG_RIP:
                target = ins.address + ins.size + op.mem.disp - img.base
                if rip_target is not None and target == rip_target:
                    return True
                if rip_range and rip_range[0] <= target < rip_range[1]:
                    return True
        return False
    total = 0
    out = []
    for name, va, size in img.executable_ranges():
        data = img.read(va, size)
        if not data:
            continue
        # A linear sweep has to survive undecodable bytes: capstone's iterator stops at the
        # first one, so resume one byte later rather than ending the section there.
        off = 0
        while off < len(data) and total < args.max:
            decoded = False
            for ins in md.disasm(data[off:], img.base + va + off):
                decoded = True
                rva = ins.address - img.base
                off = rva - va + ins.size
                if not matches(ins):
                    continue
                if args.writes_only and ins.mnemonic not in WRITE_MNEMONICS:
                    continue
                fn = img.containing(rva)
                where = f"fn {fn[0]:#x}" if fn else "?"
                out.append(f"{rva:#08x}  {ins.mnemonic:<8} {ins.op_str:<40} {name} {where}")
                total += 1
                if total >= args.max:
                    break
            if not decoded:
                off += 1
    label = (f"[{disp:#x}]" if disp is not None else
             f"[rip -> {rip_target:#x}]" if rip_target is not None else
             f"[rip in {rip_range[0]:#x}..{rip_range[1]:#x}]")
    print(f"{label} {total} reference(s)")
    for line in out:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
