"""Look for a CABAC table the decoder built at runtime, rather than the one it ships with.

The tables in the DLL are the spec's numbers in a different layout, which closed the "modified table"
explanation -- but a memory scan once found a private heap block holding `lps_range` row 4 four times,
and that has never been explained. A table built per stream into the heap would explain it, and would
also explain how one decoder reads streams no other decoder can while every table in its file is stock:
the shipped tables are not what it uses for those streams.

So this searches private memory for the row fingerprints, dumps what surrounds a hit, and reports
whether the neighbourhood looks like the range table (monotonically falling values per column) or like
something else entirely.

    python find_runtime_table.py --dump captured/runtime_table
"""

import argparse
import json
import os
import subprocess
import sys
import time

import frida

ROWS = [("row 0", "80 b0 d0 f0"), ("row 4", "74 8e a9 c3"), ("row 8", "5f 74 89 9e"),
        ("row 20", "51 3e 4d 5c")]

JS = r"""
'use strict';
var rows = ROWS;
var ranges = Process.enumerateRanges('r--').filter(function (r) { return r.size < 64 * 1024 * 1024; });
var index = 0, hits = 0;

function step() {
  var deadline = Date.now() + 400;
  while (index < ranges.length && Date.now() < deadline) {
    var range = ranges[index++];
    for (var r = 0; r < rows.length; r++) {
      var found;
      try { found = Memory.scanSync(range.base, range.size, rows[r].pattern); } catch (e) { continue; }
      for (var i = 0; i < found.length; i++) {
        hits += 1;
        var address = found[i].address;
        var before = null, after = null;
        try { before = address.sub(128).readByteArray(128); } catch (e) {}
        try { after = address.readByteArray(384); } catch (e) {}
        send({ t: 'hit', row: rows[r].name, address: address.toString(),
               before: before ? Array.prototype.map.call(new Uint8Array(before),
                 function (b) { return ('0' + b.toString(16)).slice(-2); }).join(' ') : '',
               after: after ? Array.prototype.map.call(new Uint8Array(after),
                 function (b) { return ('0' + b.toString(16)).slice(-2); }).join(' ') : '' });
      }
    }
  }
  if (index < ranges.length) setTimeout(step, 0);
  else send({ t: 'done', ranges: ranges.length, hits: hits });
}
setTimeout(step, 0);
"""


def renderer_pid():
    listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True).stdout
    best = None
    for row in listing.splitlines():
        cells = [c.strip('"') for c in row.split('","')]
        if len(cells) >= 5 and cells[0] == "EVPlayer2.exe":
            weight = int(cells[4].replace(",", "").replace(" K", ""))
            if best is None or weight > best[0]:
                best = (weight, int(cells[1]))
    return best[1] if best else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    pid = args.pid or renderer_pid()
    if not pid:
        print("EVPlayer2 is not running")
        return 1
    print(f"scanning pid {pid} for CABAC row fingerprints in private memory")

    script = frida.attach(pid).create_script(
        JS.replace("ROWS", json.dumps([{"name": name, "pattern": pattern}
                                       for name, pattern in ROWS])))
    found = []
    done = {}

    def on_message(message, data):
        if message.get("type") == "error":
            print("script error:", message.get("description"))
            return
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "hit":
            found.append(payload)
        elif payload.get("t") == "done":
            done.update(payload)

    script.on("message", on_message)
    script.load()
    deadline = time.time() + 180
    while "hits" not in done and time.time() < deadline:
        time.sleep(2)
    try:
        script.unload()
    except Exception:
        pass

    print(f"\n{len(found)} hit(s) across {done.get('ranges', 0)} range(s)")
    if args.dump and found:
        os.makedirs(args.dump, exist_ok=True)
    for entry in found[:12]:
        after = bytes.fromhex(entry["after"].replace(" ", "")) if entry["after"] else b""
        before = bytes.fromhex(entry["before"].replace(" ", "")) if entry["before"] else b""
        window = before + after
        # A range table falls steadily as the state index grows, four columns wide; anything else is
        # just a four-byte coincidence.
        pairs = sum(1 for i in range(4, len(window) - 4, 4) if window[i] <= window[i - 4])
        verdict = "looks like a table" if pairs > 40 else "no table structure"
        print(f"  {entry['row']:<8} {entry['address']}  {verdict}  "
              f"first bytes {entry['after'][:23]}")
        if args.dump:
            name = os.path.join(args.dump, entry["address"].replace("0x", "") + ".bin")
            with open(name, "wb") as handle:
                handle.write(window)
    if args.dump:
        print(f"\nwindows written under {args.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
