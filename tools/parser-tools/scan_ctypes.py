"""不注入进程、只用 ReadProcessMemory 扫描分片密钥。

frida 需要在目标进程里 VirtualAllocEx（分配内存）来加载 agent，播放器吃紧时会失败。
而读取游戏密钥只需要 PROCESS_VM_READ，用 VirtualQueryEx + ReadProcessMemory 即可，
这也正是 evmedia-win/scan.rs 该走的路径。

扫描 rw- 内存中所有 32 位十六进制串，逐个测试磁盘上的分片。
"""
import ctypes
import ctypes.wintypes as wt
import hashlib, json, os, sys, struct, time
from Crypto.Cipher import AES

DL = r"D:\Downloads\EVPlayer2Downloads"
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
MEM_COMMIT, PAGE_GUARD, PAGE_NOACCESS = 0x1000, 0x100, 0x01
PAGE_READONLY, PAGE_READWRITE, PAGE_WRITECOPY = 0x02, 0x04, 0x08
PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY = 0x20, 0x40, 0x80

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
        p = [x.strip('"') for x in line.split('","')]
        if len(p) > 1 and p[0].lower() == name.lower(): return int(p[1])
    return None

READABLE = (PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY |
            PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)

def regions(handle):
    addr, info, size = 0, MBI(), ctypes.sizeof(MBI())
    while True:
        got = k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(info), size)
        if got != size or info.RegionSize == 0: break
        if (info.State & MEM_COMMIT) and not (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) \
           and (info.Protect & READABLE) and info.Type != 0x1000000:   # 跳过 MEM_IMAGE
            yield info.BaseAddress, info.RegionSize
        nxt = info.BaseAddress + info.RegionSize
        if nxt <= addr: break
        addr = nxt

def read(handle, address, length):
    buf = ctypes.create_string_buffer(length)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, length, ctypes.byref(got)):
        return None
    return buf.raw[:got.value]

HEX = set(b"0123456789abcdefABCDEF")
def hexstrings(blob):
    out, start = set(), -1
    for i, c in enumerate(blob):
        if c in HEX:
            if start < 0: start = i
        else:
            if start >= 0 and i - start == 32:
                out.add(blob[start:i].decode().lower())
            start = -1
    return out

def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else find_pid()
    if pid is None: print("EVPlayer2.exe 未运行"); return 1
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        print("OpenProcess 失败: %d（需要管理员权限）" % ctypes.get_last_error()); return 1
    print("已打开 pid %d" % pid, flush=True)

    segs = [n for n in os.listdir(DL) if n.endswith(".ts")]
    heads, masks = {}, {}
    for n in segs:
        try:
            with open(os.path.join(DL, n), "rb") as fh: data = fh.read(16)
            if len(data) < 16: continue
            m = hashlib.md5(n.encode()).hexdigest()[:16].encode()
            masks[n] = m
            heads[n] = bytes(b ^ m[i % 16] for i, b in enumerate(data))
        except OSError: continue
    print("磁盘分片 %d（可读 %d）" % (len(segs), len(heads)), flush=True)

    t0 = time.time(); cands = set(); scanned = 0
    for base, size in regions(h):
        off = 0
        while off < size:
            chunk = min(size - off, 8 << 20)
            blob = read(h, base + off, chunk)
            if blob:
                scanned += len(blob)
                cands |= hexstrings(blob)
            off += chunk
    print("已扫 %.0f MB 内存, 得到 %d 个 32 位十六进制串 (%.1fs)"
          % (scanned / 1e6, len(cands), time.time() - t0), flush=True)

    found = {}
    for c in cands:
        aes = AES.new(c.encode(), AES.MODE_ECB)
        for n, blk in heads.items():
            if aes.decrypt(blk)[0] != 0x47: continue
            data = open(os.path.join(DL, n), "rb").read(188 * 40)
            m = masks[n]
            buf = bytes(b ^ m[i % 16] for i, b in enumerate(data))
            p = aes.decrypt(buf)
            if sum(1 for i in range(0, len(p), 188) if p[i] == 0x47) == len(p) // 188:
                found[n] = c
    print("严格验证命中: %d" % len(found), flush=True)
    json.dump({"pid": pid, "found": found}, open("captured/keys_found.json", "w"), indent=1)
    print("已写入 captured/keys_found.json", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
