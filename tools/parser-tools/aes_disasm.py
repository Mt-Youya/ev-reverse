"""Disassemble the AES region of PlayerLibRender56_vs.dll around RVA 0x792c78.

Goal: find the enclosing function's entry point and work out its calling convention, so the
routine can be hooked at its boundary (where the key and the output buffer are live) instead of
at an inner T-table load.
"""

import sys

import capstone
import pefile

DLL = r"D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll"
TARGET_RVA = 0x792C78


def main():
    pe = pefile.PE(DLL, fast_load=True)
    image_base = pe.OPTIONAL_HEADER.ImageBase
    target_off = pe.get_offset_from_rva(TARGET_RVA)
    print("image base   : 0x%X" % image_base)
    print("target RVA   : 0x%X" % TARGET_RVA)
    print("target VA    : 0x%X" % (image_base + TARGET_RVA))
    print("file offset  : 0x%X" % target_off)

    with open(DLL, "rb") as handle:
        blob = handle.read()

    # Walk backwards looking for the padding compilers leave between functions.
    start = target_off
    while start > target_off - 0x1200:
        if blob[start:start + 4] in (b"\xCC\xCC\xCC\xCC", b"\x00\x00\x00\x00"):
            start += 4
            break
        start -= 1
    print("probable fn start near file offset 0x%X (RVA 0x%X)" % (start, pe.get_rva_from_offset(start)))

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True

    window_start = start
    window = blob[window_start:target_off + 0x180]
    for insn in md.disasm(window, image_base + pe.get_rva_from_offset(window_start)):
        marker = "  <== T-table load" if insn.address == image_base + TARGET_RVA else ""
        print("0x%X  %-24s %s %s%s" % (
            insn.address,
            insn.bytes.hex(),
            insn.mnemonic,
            insn.op_str,
            marker,
        ))
        if insn.mnemonic == "ret":
            print("---- function ends here ----")


if __name__ == "__main__":
    main()
