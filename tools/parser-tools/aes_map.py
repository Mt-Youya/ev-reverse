"""Map the AES-related functions in PlayerLibRender56_vs.dll.

The previous script found an OpenSSL-style AES_set_encrypt_key at RVA 0x791F90. This one walks a
region of .text, finds function boundaries via the padding compilers emit, and prints each
prologue plus a signature hint, so every AES entry point (set_key, encrypt, decrypt, and the
ECB/CBC wrappers) can be identified and hooked.
"""

import capstone
import pefile

DLL = r"D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll"
START_RVA = 0x791000
END_RVA = 0x794200

# Instructions that only ever appear in the OpenSSL AES key-schedule byte swap.
KEY_SCHEDULE_TELLS = ("0xff00ff", "0xff00ff00")


def function_starts(blob, pe, start_off, end_off):
    """Yield file offsets that begin a function: preceded by padding or a ret."""
    offset = start_off
    while offset < end_off:
        if blob[offset - 1:offset + 1] in (b"\xc3\xcc", b"\xc3\x00") or blob[offset - 4:offset] == b"\xcc\xcc\xcc\xcc":
            yield offset
        offset += 1


def main():
    pe = pefile.PE(DLL, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    with open(DLL, "rb") as handle:
        blob = handle.read()

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True

    start_off = pe.get_offset_from_rva(START_RVA)
    end_off = pe.get_offset_from_rva(END_RVA)

    print("scanning RVA 0x%X..0x%X for function boundaries\n" % (START_RVA, END_RVA))

    starts = sorted(set(function_starts(blob, pe, start_off, end_off)))
    for index, offset in enumerate(starts):
        rva = pe.get_rva_from_offset(offset)
        stop = starts[index + 1] if index + 1 < len(starts) else end_off
        body = blob[offset:stop]
        insns = list(md.disasm(body, base + rva))
        if not insns:
            continue
        text = " ".join("%s %s" % (i.mnemonic, i.op_str) for i in insns)
        hints = []
        for tell in KEY_SCHEDULE_TELLS:
            if tell in text:
                hints.append("KEY-SCHEDULE byte swap")
                break
        if "aesenc" in text or "aesenclast" in text:
            hints.append("AES-NI instruction")
        if "aesdec" in text:
            hints.append("AES-NI decrypt")
        if "[r14 + 0xf0]" in text or "0xf0]" in text:
            hints.append("rounds field at ctx+0xf0")
        size = stop - offset
        if not hints and size < 64:
            continue
        print("RVA 0x%-8X size %-6d %s" % (rva, size, " | ".join(hints) if hints else ""))
        for insn in insns[:14]:
            print("      %-22s %s %s" % (insn.bytes.hex(), insn.mnemonic, insn.op_str))
        print()


if __name__ == "__main__":
    main()
