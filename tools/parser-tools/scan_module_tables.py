"""Ask the *running* decoder whether it holds the stock CABAC tables, rather than its file on disk.

The player's DLL has no copy of FFmpeg's H.264 `lps_range` table anywhere in the file, while the stock
4.2.2 build on this machine has two. That is either a modified decoder or a table that only exists
once the module is loaded -- some packers decrypt a section in place, and a table built at runtime
would look exactly the same from disk.

So: read the loaded module's own bytes and search its memory for the stock table and for the
neighbouring tables. Bytes at `base + 0xB9648` are compared with the file first, to establish that the
file on disk is the file in memory before any conclusion is drawn from searching it.

    python scan_module_tables.py <path to the dll on disk>
"""

import subprocess
import sys

import frida

LPS = "80 b0 d0 f0 80 a7 c5 e3 80 9e bb d8"
NEIGHBOURS = [
    ("table before lps_range", "00 03 06 08 0b 0e 11 14 17 1a 1d 20 24 27 2a 2d"),
    ("lps_range rows 3-5", "7b 96 b2 cd 74 8e a9 c3 6f 87 a0 b9"),
    ("lps_range rows 6-8", "69 80 98 af 64 7a 90 a6 5f 74 89 9e"),
]

JS = r"""
'use strict';
var mod = Process.getModuleByName('PlayerLibRender56_vs.dll');
var out = { base: mod.base.toString(), size: mod.size, path: mod.path };
try {
  out.entryBytes = Array.prototype.map.call(new Uint8Array(mod.base.add(0xB9648).readByteArray(32)),
    function (b) { return ('0' + b.toString(16)).slice(-2); }).join(' ');
} catch (e) { out.entryError = String(e); }

var patterns = PATTERNS;
out.scans = [];
for (var i = 0; i < patterns.length; i++) {
  var hits = [];
  try {
    var found = Memory.scanSync(mod.base, mod.size, patterns[i].pattern);
    for (var k = 0; k < found.length && k < 6; k++) hits.push(found[k].address.toString());
  } catch (e) { hits.push('error ' + e); }
  out.scans.push({ name: patterns[i].name, hits: hits });
}
send(out);
"""


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll"
    listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True).stdout
    pids = []
    for row in listing.splitlines():
        cells = [c.strip('"') for c in row.split('","')]
        if len(cells) >= 5 and cells[0] == "EVPlayer2.exe":
            pids.append((int(cells[4].replace(",", "").replace(" K", "")) * 1024, int(cells[1])))
    if not pids:
        print("no EVPlayer2 process is running")
        return 1
    pid = sorted(pids, reverse=True)[0][1]
    print(f"attaching to pid {pid}")

    disk = open(path, "rb").read()
    disk_entry = disk[0xB9648:0xB9648 + 32]
    print(f"file on disk at 0xB9648: {disk_entry.hex(' ')}")

    patterns = [{"name": "stock lps_range rows 0-2", "pattern": LPS}]
    for name, pattern in NEIGHBOURS:
        patterns.append({"name": name, "pattern": pattern})

    script = frida.attach(pid).create_script(
        JS.replace("PATTERNS", __import__("json").dumps(patterns)))
    result = {}

    def on_message(message, data):
        if message.get("type") == "send":
            result.update(message["payload"])
        elif message.get("type") == "error":
            print("script error:", message.get("description"))

    script.on("message", on_message)
    script.load()
    import time
    time.sleep(3)
    try:
        script.unload()
    except Exception:
        pass

    if not result:
        print("no answer from the script")
        return 1
    print(f"module base {result['base']}  size {result['size']:,}  path {result.get('path')}")
    entry = result.get("entryBytes")
    print(f"module memory at 0xB9648: {entry}")
    if entry:
        print("  -> " + ("identical to the file: this is the same module"
                         if entry == disk_entry.hex(" ") else
                         "DIFFERENT from the file: the loaded image is not this file"))
    print()
    for scan in result["scans"]:
        print(f"  {scan['name']:<26} {len(scan['hits'])} hit(s) {scan['hits']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
