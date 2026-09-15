"""The decryptor family, its vtables, and which of them reach the two silent call sites.

The DLL carries nine media decryptors (`Decryptor_V3A`, `V4A`, `V8A`, `EV4`..`EV7`, `EVS`), each a
class with its own vtable. The two decryption call sites that never fire in any capture turned out
to be methods of two of them, which is why no amount of playing the account's own courses reached
them: the courses are one format, and these classes are the others.

Every vtable is found the same way: a type descriptor named `.?AVDecryptor_*@` is referenced by a
Complete Object Locator, and the vtable sits eight bytes after it.

    python crypto_classes.py <dll>
"""

import argparse
import bisect
import re
import struct
import sys

IMAGE = 0x180000000
SILENT = {0x1B480: "0x1B480 (never fires)", 0x34390: "0x34390 (never fires)"}
NEAR = {0x1EA10: "response decrypt 0x1EA10"}


class Image:
    def __init__(self, path):
        self.data = open(path, "rb").read()
        self.base = IMAGE
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        count = struct.unpack_from("<H", self.data, pe + 6)[0]
        opt = struct.unpack_from("<H", self.data, pe + 20)[0]
        self.sections = []
        for i in range(count):
            at = pe + 24 + opt + i * 40
            name = self.data[at:at + 8].rstrip(b"\x00").decode("latin1")
            vsize, rva, rawsize, raw = struct.unpack_from("<IIII", self.data, at + 8)
            self.sections.append((name, rva, vsize, raw, rawsize))
        self.functions = []
        for name, rva, vsize, raw, rawsize in self.sections:
            if name != ".pdata":
                continue
            for i in range(0, rawsize, 12):
                start, end, _ = struct.unpack_from("<III", self.data, raw + i)
                self.functions.append((start, end))
        self.functions.sort()

    def read(self, rva, size):
        for name, base, vsize, raw, rawsize in self.sections:
            if base <= rva < base + max(vsize, rawsize):
                return self.data[raw + (rva - base):raw + (rva - base) + size]
        return b""

    def function_at(self, rva):
        i = bisect.bisect_right(self.functions, (rva, 1 << 32)) - 1
        if i >= 0 and self.functions[i][0] <= rva < self.functions[i][1]:
            return self.functions[i]
        return None

    def rva_of_offset(self, offset):
        for name, base, vsize, raw, rawsize in self.sections:
            if raw <= offset < raw + rawsize:
                return base + (offset - raw)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    args = ap.parse_args()
    img = Image(args.dll)

    descriptors = {}
    for match in re.finditer(rb"\.\?AV([A-Za-z0-9_@?$]+)@@", img.data):
        descriptors[img.rva_of_offset(match.start())] = match.group(1).decode("latin1")

    # A COL's fourth dword points at the *type descriptor structure*, whose name field starts 16
    # bytes in, so the value to search for is the name's RVA minus 16 -- not the name's RVA. A
    # class can own more than one COL, and the vtable is the address whose preceding qword holds
    # that COL's *virtual address*.
    tables = {}
    for td_rva, name in descriptors.items():
        needle = struct.pack("<I", td_rva - 16)
        start = 0
        while True:
            at = img.data.find(needle, start)
            if at < 0:
                break
            start = at + 1
            col_rva = img.rva_of_offset(at - 12)
            if col_rva is None:
                continue
            signature = struct.unpack_from("<I", img.read(col_rva, 4))[0]
            self_rva = struct.unpack_from("<I", img.read(col_rva + 20, 4))[0]
            if signature != 1 or self_rva != col_rva:
                continue
            back = struct.pack("<Q", col_rva + IMAGE)
            where = img.data.find(back)
            while where >= 0:
                vtable = img.rva_of_offset(where + 8)
                if vtable is not None:
                    tables[vtable] = name
                where = img.data.find(back, where + 1)

    print("{} class(es) with a vtable: {}".format(len(tables), ", ".join(sorted(tables.values()))))
    print()

    text = [entry for entry in img.sections if entry[0] == ".text"]
    low, high = (text[0][1], text[0][1] + max(text[0][2], text[0][4])) if text else (0x1000, 0x7A0000)

    for vtable_rva in sorted(tables):
        name = tables[vtable_rva]
        slots = []
        for slot in range(24):
            value = struct.unpack_from("<Q", img.read(vtable_rva + slot * 8, 8))[0]
            rva = value - IMAGE
            # A method that has no unwind data (`0x9AC0` is one) is still a method: only a value
            # outside the code section ends the table.
            if not (low <= rva < high):
                break
            slots.append((slot, rva, img.function_at(rva)))
        marks = []
        for slot, rva, where in slots:
            if rva in SILENT:
                marks.append("slot {} = {}".format(slot, SILENT[rva]))
            elif rva in NEAR:
                marks.append("slot {} = {}".format(slot, NEAR[rva]))
        print("=== {}  vtable 0x{:x}  {} slot(s) {}{} ===".format(
            name, vtable_rva, len(slots), " ".join(marks), "" if marks else ""))
        for slot, rva, where in slots:
            tag = "   <-- THE SILENT ONE" if rva in SILENT else ""
            span = "fn 0x{:x}..0x{:x}".format(where[0], where[1]) if where else "no .pdata"
            print("  slot {:2d}  0x{:<6x} {}{}".format(slot, rva, span, tag))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
