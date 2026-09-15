"""判定悬而未决的问题：播放上下文 +0x120 那个槽位，到底是不是密钥源？

背景：这个槽位曾被当作分片密钥的来源用了很久。后来的结论是「从它推导出来的值从来没有
真正打开过分片」，而所有实际可用的密钥都来自「拿堆里的 32 位十六进制串去试分片字节」。
但那份结论的证据是「成功的密钥都来自试解」——那是关于来源的统计，不是「槽位里没有密钥」
的证明。

本脚本做的是直接的判定实验。它不注入进程，只用 ReadProcessMemory：

  1. 找到 PlayerLibRender56_vs.dll 的基址，定位那些 vtable 落在 [base+0x802000, base+0x804000)
     范围内的播放上下文对象；
  2. 从每个对象读出索引（+0x08）、文件名（+0x18）和 32 字节槽位（+0x120）；
  3. 槽位非空（不是 0xBAADF00D、不是全零）时，按 mix 逆变换推出一个 32 位十六进制串；
  4. **拿这个串当 AES-256 密钥去解密缓存里对应的分片**，看它是不是真的能打开。

结论只有两种：
  * 能打开 —— 槽位确实是密钥源，之前「从未打开过分片」的结论是错的；
  * 打不开 —— 槽位不是密钥源，之前删掉它是正确的。

需要一个「已经播放过、缓存里还有密文」的现场：脚本会报告它找到了多少个已填充的槽位，
以及其中有多少个真的解开了分片。一个都没有时，说明播放器当前没有解密任何分片，
请在播放某个课时之后再跑一次。

用法：
    python probe_schedule_key.py [pid]
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import sys

from Crypto.Cipher import AES

CACHE = r"D:\Downloads\EVPlayer2Downloads"
DLL = "playerlibrender56_vs.dll"
VTABLE_LOW, VTABLE_HIGH = 0x802000, 0x804000
CONTEXT_SIZE = 0x2A8
OFF_INDEX, OFF_FILE, OFF_SCHEDULE = 0x08, 0x18, 0x120
HEAP_FILL = bytes.fromhex("0df0adba")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
MEM_COMMIT, PAGE_GUARD, PAGE_NOACCESS = 0x1000, 0x100, 0x01
MEM_IMAGE = 0x1000000
READABLE = 0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80

# A handle is pointer-sized and a module handle is an address. Left to ctypes' defaults both
# arrive as C ints and the high half is dropped, which shows up as an OverflowError rather than
# as a wrong answer -- so they are declared rather than assumed.
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD,
                                       ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong), ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", ctypes.c_ulong), ("__a1", ctypes.c_ulong),
                ("RegionSize", ctypes.c_ulonglong), ("State", ctypes.c_ulong),
                ("Protect", ctypes.c_ulong), ("Type", ctypes.c_ulong), ("__a2", ctypes.c_ulong)]


def find_pid(name="EVPlayer2.exe"):
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % name, "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == name.lower():
            return int(parts[1])
    return None


def module_base(handle, wanted):
    """加载进目标进程的某个模块的基址。"""
    arr = (ctypes.c_void_p * 1024)()
    needed = ctypes.c_ulong(0)
    if not psapi.EnumProcessModulesEx(handle, arr, ctypes.sizeof(arr), ctypes.byref(needed), 0x03):
        return None
    name = ctypes.create_unicode_buffer(260)
    for i in range(needed.value // ctypes.sizeof(ctypes.c_void_p)):
        if psapi.GetModuleBaseNameW(handle, arr[i], name, 260):
            if name.value.lower() == wanted:
                return arr[i]
    return None


def regions(handle):
    addr, info = 0, MBI()
    size = ctypes.sizeof(MBI)
    while True:
        got = k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(info), size)
        if got != size or info.RegionSize == 0:
            break
        if (info.State & MEM_COMMIT) and not (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) \
           and (info.Protect & READABLE) and info.Type != MEM_IMAGE:
            yield info.BaseAddress, info.RegionSize
        nxt = info.BaseAddress + info.RegionSize
        if nxt <= addr:
            break
        addr = nxt


def read(handle, address, length):
    buf = ctypes.create_string_buffer(length)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, length, ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


def string_field(handle, obj, offset):
    """播放上下文的字符串布局：{inline[16] | 指针, len@+16, cap@+24}。"""
    length = int.from_bytes(obj[offset + 16:offset + 24], "little")
    cap = int.from_bytes(obj[offset + 24:offset + 32], "little")
    if length > 8192 or cap < length or cap > 1 << 20:
        return None
    if cap < 16:
        raw = obj[offset:offset + length]
    else:
        raw = read(handle, int.from_bytes(obj[offset:offset + 8], "little"), length)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def xtime(value):
    return ((value << 1) ^ (0x1B if value & 0x80 else 0)) & 0xFF


def un_mix_columns(block):
    """播放器对密钥后半段做的 MixColumns 的逆。"""
    out = bytearray(16)
    for base in range(0, 16, 4):
        t = block[base] ^ block[base + 1] ^ block[base + 2] ^ block[base + 3]
        for i in range(4):
            out[base + i] = block[base + i] ^ t ^ xtime(block[base + i] ^ block[base + (i + 1) % 4])
    return bytes(out)


def key_from_schedule(schedule):
    """把 32 字节槽位还原成一个 32 位十六进制串，不是密钥就返回 None。"""
    if schedule[:4] == HEAP_FILL or not any(schedule):
        return None
    text = schedule[:16] + un_mix_columns(schedule[16:32])
    try:
        candidate = text.decode("ascii")
    except UnicodeDecodeError:
        return None
    return candidate if len(candidate) == 32 and all(c in "0123456789abcdefABCDEF" for c in candidate) else None


def opens(key_text, path, head):
    """这个密钥能打开这个分片吗：先异或掩码，再 AES-256-ECB 解密，然后看 188 字节对齐。"""
    name = os.path.basename(path)
    mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
    data = open(path, "rb").read(head)
    if not data or len(data) % 16:
        return False
    buf = bytes(b ^ mask[i % 16] for i, b in enumerate(data))
    plain = AES.new(key_text.encode(), AES.MODE_ECB).decrypt(buf)
    return plain[0] == 0x47 and all(plain[i] == 0x47 for i in range(0, len(plain) - 187, 188))


def contexts(handle, base):
    """所有 vtable 落在该 DLL 指定范围内的播放上下文对象。"""
    low, high = base + VTABLE_LOW, base + VTABLE_HIGH
    found = []
    for region, size in regions(handle):
        offset = 0
        while offset < size:
            chunk = min(size - offset, 8 << 20)
            blob = read(handle, region + offset, chunk)
            if blob:
                for at in range(0, len(blob) - 8, 8):
                    pointer = int.from_bytes(blob[at:at + 8], "little")
                    if low <= pointer < high:
                        obj = read(handle, region + offset + at, CONTEXT_SIZE)
                        if obj and len(obj) == CONTEXT_SIZE:
                            found.append(obj)
            offset += chunk
    return found


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else find_pid()
    if pid is None:
        print("EVPlayer2.exe 未运行")
        return 1
    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print("OpenProcess 失败：%d（需要管理员权限）" % ctypes.get_last_error())
        return 1
    print("已打开 pid %d" % pid, flush=True)

    base = module_base(handle, DLL)
    if base is None:
        print("%s 未加载" % DLL)
        return 1
    print("模块基址 0x%x" % base, flush=True)

    objects = contexts(handle, base)
    print("播放上下文对象：%d" % len(objects), flush=True)

    ready, derived, opened, unopened = 0, 0, 0, 0
    for obj in objects:
        file = string_field(handle, obj, OFF_FILE)
        if not file or not file.endswith(".ts"):
            continue
        schedule = obj[OFF_SCHEDULE:OFF_SCHEDULE + 32]
        if schedule[:4] == HEAP_FILL or not any(schedule):
            continue
        ready += 1
        text = key_from_schedule(schedule)
        if text is None:
            continue
        derived += 1
        path = os.path.join(CACHE, file)
        if not os.path.exists(path):
            continue
        index = int.from_bytes(obj[OFF_INDEX:OFF_INDEX + 4], "little")
        if opens(text, path, 188 * 40):
            opened += 1
            print("  解开  index=%-5d key=%s  %s" % (index, text, file[:44]), flush=True)
            if opened == 1:
                # A real (schedule, key) pair, for the regression test in
                # crates/evmedia-core/tests/crypto.rs. Recorded rather than synthesised: the
                # derivation was once declared dead on the strength of a broken test, and this
                # is the evidence that it is not.
                print("  schedule[] = \"%s\"" % schedule.hex(), flush=True)
                print("  file       = \"%s\"" % file, flush=True)
        else:
            unopened += 1
            if unopened <= 5:
                print("  打不开 index=%-5d key=%s  %s" % (index, text, file[:44]), flush=True)

    print()
    print("槽位已填充：%d，其中能还原成 32 位十六进制串：%d" % (ready, derived))
    print("  真的解开了分片：%d" % opened)
    print("  解不开：      %d" % unopened)
    if ready == 0:
        print()
        print("播放器当前没有解密任何分片，这个实验无从判定。播放某个课时之后再跑一次。")
    elif opened:
        print()
        print("结论：槽位确实是密钥源。之前「从未打开过分片」的结论是错的。")
    elif unopened:
        print()
        print("结论：槽位里的值还原不出可用的密钥。它不是密钥源，删掉那条读法是对的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
