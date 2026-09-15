"""Recover the API's AES key from the live EVPlayer2 process.

The player statically links OpenSSL's AES. `AES_set_encrypt_key` (RVA 0x791F90) fills an
`AES_KEY` whose layout ends with the round count at +0xF0 -- 10, 12 or 14. A random 4-byte word
is essentially never exactly one of those, so scanning for that field finds AES contexts with
almost no false positives, and the first 16/24/32 bytes of the context are the user key (stored
big-endian per word, because that is what the T-table implementation wants).

Keys are then tested by decrypting a captured `params` blob, which is plain AES-ECB over JSON.
"""

import base64
import ctypes
import glob
import json
import os
import struct
import sys

from Crypto.Cipher import AES

KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01

ROUNDS_OFFSET = 0xF0
ROUNDS_TO_KEYLEN = {10: 16, 12: 24, 14: 32}


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", ctypes.c_ulong),
        ("__alignment1", ctypes.c_ulong),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", ctypes.c_ulong),
        ("Protect", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
        ("__alignment2", ctypes.c_ulong),
    ]


def find_pid(name="EVPlayer2.exe"):
    import subprocess

    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % name, "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == name.lower():
            return int(parts[1])
    return None


def regions(handle):
    address = 0
    info = MEMORY_BASIC_INFORMATION64()
    size = ctypes.sizeof(info)
    while True:
        got = KERNEL32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(info), size)
        if got != size or info.RegionSize == 0:
            break
        base = info.BaseAddress
        if (info.State & MEM_COMMIT) and not (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)):
            yield base, info.RegionSize
        nxt = base + info.RegionSize
        if nxt <= address:
            break
        address = nxt


def read(handle, address, length):
    buf = ctypes.create_string_buffer(length)
    got = ctypes.c_size_t(0)
    ok = KERNEL32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, length, ctypes.byref(got))
    if not ok or got.value != length:
        return None
    return buf.raw


def swap_words(raw):
    """OpenSSL stores the key big-endian per 32-bit word; undo that."""
    out = bytearray()
    for i in range(0, len(raw) - len(raw) % 4, 4):
        out += raw[i:i + 4][::-1]
    return bytes(out)


def candidates(handle):
    seen = set()
    for base, size in regions(handle):
        cursor = base
        while cursor < base + size:
            chunk = min(base + size - cursor, 8 << 20)
            blob = read(handle, cursor, chunk)
            if blob:
                for i in range(0, len(blob) - 4, 4):
                    rounds = struct.unpack_from("<I", blob, i)[0]
                    if rounds in ROUNDS_TO_KEYLEN:
                        ctx = cursor + i - ROUNDS_OFFSET
                        if ctx in seen or i < ROUNDS_OFFSET:
                            continue
                        seen.add(ctx)
                        yield ctx, rounds
            cursor += chunk


def looks_like_json(data):
    if not data or data[0:1] != b"{":
        return False
    try:
        text = data.rstrip(b"\x00")
        json.loads(text.decode("utf-8"))
        return True
    except Exception:
        # Even if it does not parse, a payload that is almost all printable is worth reporting.
        printable = sum(1 for b in data if 9 <= b <= 13 or 32 <= b < 127)
        return printable / len(data) > 0.95


def main():
    targets = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "captured", "bodies", "getDownEVSKey-*.params")))
    if not targets:
        print("no captured params; run extract_bodies.py first")
        return 1
    blob = base64.b64decode(json.loads(open(targets[0], "rb").read())["params"])
    print("validation target: %s (%d bytes, AES-ECB)" % (os.path.basename(targets[0]), len(blob)))

    pid = find_pid()
    if pid is None:
        print("EVPlayer2.exe is not running")
        return 1
    handle = KERNEL32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print("OpenProcess failed: %d" % ctypes.get_last_error())
        return 1
    print("attached to pid %d" % pid)

    found = 0
    tried = 0
    for ctx, rounds in candidates(handle):
        key_len = ROUNDS_TO_KEYLEN[rounds]
        material = read(handle, ctx, key_len)
        if not material:
            continue
        for label, key in (("words swapped", swap_words(material)), ("raw", material)):
            tried += 1
            try:
                plain = AES.new(key, AES.MODE_ECB).decrypt(blob)
            except Exception:
                continue
            if looks_like_json(plain):
                found += 1
                print()
                print("*** KEY FOUND ***")
                print("  context   : 0x%X  (rounds=%d)" % (ctx, rounds))
                print("  key (%s, %d bytes): %s" % (label, key_len, key.hex()))
                print("  plaintext : %s" % plain[:400])
                if found >= 3:
                    return 0
        if not material:
            pass

    print()
    print("scanned; %d candidate key(s) tried, %d match(es)" % (tried, found))
    if not found:
        print("No AES context in memory opens a captured params blob.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
