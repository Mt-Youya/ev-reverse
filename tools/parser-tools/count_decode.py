"""Count how often each EVPlayer2 process actually decodes, and list what it has loaded.

Segments can be fetched and keys set by a process that never decodes anything -- the keys arriving
from one PID while no frame is ever produced is exactly that shape. This attaches read-only, counts
calls into `h264_decode_frame` (RVA 0xB9648) for a few seconds per process, and reports module base
addresses, so "the player is playing" can be established rather than assumed.

    python count_decode.py --seconds 10
"""

import argparse
import subprocess
import sys
import time

import frida

JS = r"""
'use strict';
var mods = Process.enumerateModules().filter(function (m) { return /PlayerLib|ffmpeg|avcodec/i.test(m.name); });
send({ t: 'modules', mods: mods.map(function (m) { return { name: m.name, base: m.base.toString(), size: m.size }; }) });
var dll = null;
try { dll = Process.getModuleByName('PlayerLibRender56_vs.dll'); } catch (e) {}
if (dll) {
  var calls = 0;
  Interceptor.attach(dll.base.add(0xB9648), { onEnter: function () { calls += 1; } });
  setInterval(function () { send({ t: 'count', calls: calls }); }, 2000);
} else {
  send({ t: 'error', message: 'PlayerLibRender56_vs.dll not loaded' });
}
"""


def pids():
    """EVPlayer2 runs as a small launcher plus a heavy renderer; the renderer is the one that
    decodes, so pick by working set rather than by whichever PID tasklist lists first."""
    listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True).stdout
    out = []
    for row in listing.splitlines():
        cells = [c.strip('"') for c in row.split('","')]
        if len(cells) >= 5 and cells[0] == "EVPlayer2.exe":
            out.append((int(cells[4].replace(",", "").replace(" K", "")) * 1024, int(cells[1])))
    return [pid for _, pid in sorted(out, reverse=True)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, action="append")
    ap.add_argument("--seconds", type=float, default=10)
    args = ap.parse_args()

    for pid in (args.pid or pids()):
        print(f"\n=== pid {pid}")
        try:
            session = frida.attach(pid)
        except Exception as exc:
            print(f"  attach failed: {exc}")
            continue
        script = session.create_script(JS)

        def on_message(message, data, pid=pid):
            if message.get("type") == "error":
                print(f"  [{pid}] script error: {message.get('description')}")
                return
            if message.get("type") != "send":
                return
            payload = message["payload"]
            if payload.get("t") == "modules":
                for mod in payload["mods"]:
                    print(f"  module {mod['name']} base={mod['base']} size={mod['size']:,}")
            elif payload.get("t") == "count":
                print(f"  [{pid}] decode calls so far: {payload['calls']}")
            elif payload.get("t") == "error":
                print(f"  [{pid}] {payload['message']}")

        script.on("message", on_message)
        try:
            script.load()
        except Exception as exc:
            print(f"  load failed: {exc}")
            session.detach()
            continue
        time.sleep(args.seconds)
        try:
            session.detach()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
