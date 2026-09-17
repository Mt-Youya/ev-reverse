"""Find which module in the player holds the standard CABAC table -- if any module does.

The player's `PlayerLibRender56_vs.dll` contains no row of the H.264 spec's `rangeTabLPS`, yet the
player plays streams that stock FFmpeg decodes with exactly that table. Two decoders is the obvious way
both can be true, so this asks the question per module instead of per file: enumerate every loaded
module, scan each one's memory for the table, and report which modules have it. A hit names the stock
decoder; no hit at all means the standard table never exists in this process, which is its own answer.

    python find_stock_cabac.py
"""

import subprocess
import sys
import time

import frida

ROWS = [
    ("rows 0-2", "80 b0 d0 f0 80 a7 c5 e3 80 9e bb d8"),
    ("row 0", "80 b0 d0 f0"),
    ("row 4", "74 8e a9 c3"),
    ("row 8", "5f 74 89 9e"),
]

JS = r"""
'use strict';
var rows = ROWS;

// Scan in slices and yield between them. Doing it inline blocks the target's JS thread until every
// range has been walked, and Frida's own load() times out before the script ever finishes -- which
// reads as "the tool is broken" rather than "the scan is slow".
function moduleRanges() {
  var out = [];
  var modules = Process.enumerateModules();
  for (var i = 0; i < modules.length; i++) out.push([modules[i].base, modules[i].base.add(modules[i].size)]);
  return out;
}

function insideModules(address, ranges) {
  for (var i = 0; i < ranges.length; i++) {
    if (address.compare(ranges[i][0]) >= 0 && address.compare(ranges[i][1]) < 0) return true;
  }
  return false;
}

var all = Process.enumerateRanges('r--');
var skip = moduleRanges();
var queue = [];
for (var i = 0; i < all.length; i++) {
  var r = all[i];
  if (r.size > 64 * 1024 * 1024) continue;
  if (insideModules(r.base, skip)) continue;         // modules were already checked
  queue.push(r);
}

var index = 0, bytes = 0, found = 0;
send({ t: 'plan', ranges: queue.length, rangesTotal: all.length });

function step() {
  var deadline = Date.now() + 400;
  while (index < queue.length && Date.now() < deadline) {
    var range = queue[index++];
    bytes += range.size;
    var hits = {};
    var any = false;
    for (var r = 0; r < rows.length; r++) {
      var addresses = [];
      try {
        var matches = Memory.scanSync(range.base, range.size, rows[r].pattern);
        for (var k = 0; k < matches.length && k < 4; k++) addresses.push(matches[k].address.toString());
      } catch (e) { }
      if (addresses.length) { hits[rows[r].name] = addresses; any = true; }
    }
    if (any) {
      found += 1;
      send({ t: 'hit', base: range.base.toString(), size: range.size,
             protection: range.protection, hits: hits });
    }
  }
  if (index < queue.length) { setTimeout(step, 0); }
  else { send({ t: 'done', count: queue.length, bytes: bytes, found: found }); }
}
setTimeout(step, 0);
"""


def renderer_pids():
    listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True).stdout
    out = []
    for row in listing.splitlines():
        cells = [c.strip('"') for c in row.split('","')]
        if len(cells) >= 5 and cells[0] == "EVPlayer2.exe":
            out.append((int(cells[4].replace(",", "").replace(" K", "")) * 1024, int(cells[1])))
    return [pid for _, pid in sorted(out, reverse=True)]


def main():
    pids = renderer_pids()
    if not pids:
        print("no EVPlayer2 process is running")
        return 1
    pid = pids[0]
    print(f"attaching to pid {pid}")
    script = frida.attach(pid).create_script(
        JS.replace("ROWS", __import__("json").dumps(
            [{"name": name, "pattern": pattern} for name, pattern in ROWS])))
    modules = []
    done = {}
    hits = []

    def on_message(message, data):
        if message.get("type") == "error":
            print("script error:", message.get("description"))
            return
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "plan":
            print(f"{payload['ranges']} private range(s) to scan "
                  f"(of {payload['rangesTotal']} readable ranges)")
        elif payload.get("t") == "hit":
            hits.append(payload)
            print(f"  HIT at {payload['base']} +0x{payload['size']:x}  {payload['hits']}")
        elif payload.get("t") == "done":
            done.update(payload)

    script.on("message", on_message)
    script.load()
    deadline = time.time() + 240
    while "count" not in done and time.time() < deadline:
        time.sleep(2)
    try:
        script.unload()
    except Exception:
        pass

    print(f"\nscanned {done.get('count')} range(s), "
          f"{(done.get('bytes') or 0) / 1048576:.0f} MB, {len(hits)} range(s) with a hit")
    if not hits:
        print("the standard CABAC table is not in this process outside its modules either")
    return 0


if __name__ == "__main__":
    sys.exit(main())
