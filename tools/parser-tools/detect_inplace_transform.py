"""Ask whether the player rewrites the packet in place before decoding it.

The player's decoder turns bytes into a picture that our ffmpeg turns into flat grey, yet the bytes
handed to `h264_decode_frame` are the ones we decrypt. One way both can be true is that the decoder
is patched and undoes a second layer on the way in -- a transform we never see, because we read the
packet at function entry, before it runs. That is testable without reversing anything: read the
packet's bytes again when the call returns. Unchanged means the work happens on a copy somewhere
else; changed means the transform is in place, and the before/after bytes are the transform.

    python detect_inplace_transform.py --pid 10188 --seconds 20
"""

import argparse
import json
import os
import sys
import subprocess
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "inplace")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var calls = 0, changed = 0, kept = 0;

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    this.pkt = args[3];
    this.size = -1; this.data = null; this.before = null; this.pts = 0;
    calls += 1;
    try {
      if (this.pkt.isNull()) return;
      this.size = this.pkt.add(0x20).readS32();
      this.pts = this.pkt.add(0x08).readS64().toNumber();
      this.data = this.pkt.add(0x18).readPointer();
      if (this.size <= 0 || this.size > 4 * 1024 * 1024 || this.data.isNull()) return;
      this.before = this.data.readByteArray(this.size);
    } catch (e) { this.before = null; }
  },
  onLeave: function (retval) {
    if (!this.before) return;
    try {
      var after = this.data.readByteArray(this.size);
      var a = new Uint8Array(this.before), b = new Uint8Array(after);
      var diffs = 0, first = -1, last = -1, xors = {};
      for (var i = 0; i < a.length; i++) {
        if (a[i] !== b[i]) {
          diffs++;
          if (first < 0) first = i;
          last = i;
          var x = a[i] ^ b[i];
          xors[x] = (xors[x] || 0) + 1;
        }
      }
      var summary = { t: 'cmp', call: calls, pts: this.pts, size: this.size, diffs: diffs,
                      first: first, last: last, retval: retval.toInt32(),
                      head: Array.prototype.slice.call(b, 0, 16) };
      if (diffs) { changed++; summary.xor = xors; }
      send(summary);
      if (diffs && kept < 4 && this.size > 4096) {
        kept++;
        send({ t: 'bytes', call: calls, size: this.size, changed_bytes: diffs },
             this.data.readByteArray(Math.min(this.size, 262144)));
      }
    } catch (e) { send({ t: 'cmp', error: String(e), call: calls }); }
  }
});

setInterval(function () { send({ t: 'stats', calls: calls, changed: changed }); }, 5000);
send({ t: 'armed', base: dll.base.toString() });
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, action="append")
    ap.add_argument("--seconds", type=float, default=20)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    log = open(os.path.join(OUT, "events.jsonl"), "w", encoding="utf-8")
    scripts = []

    for pid in (args.pid or renderer_pids()[:1]):
        script = frida.attach(pid).create_script(JS)

        def on_message(message, data, pid=pid):
            if message.get("type") == "error":
                print(f"[{pid}] script error: {message.get('description')}", flush=True)
                return
            if message.get("type") != "send":
                return
            payload = message["payload"]
            log.write(json.dumps(payload) + "\n")
            log.flush()
            if payload.get("t") == "armed":
                print(f"[{pid}] armed at {payload['base']}", flush=True)
            elif payload.get("t") == "stats":
                print(f"[{pid}] calls={payload['calls']} changed={payload['changed']}", flush=True)
            elif payload.get("t") == "bytes":
                name = os.path.join(OUT, f"after_{payload['call']:05d}_{payload['size']}.bin")
                with open(name, "wb") as out:
                    out.write(bytes(data))
                print(f"[{pid}] saved {name} ({payload['changed_bytes']} bytes differ)", flush=True)
            elif payload.get("t") == "cmp" and payload.get("diffs", 0) > 0:
                print(f"[{pid}] call {payload['call']} size={payload['size']} "
                      f"diffs={payload['diffs']} first={payload['first']} last={payload['last']} "
                      f"xor={payload.get('xor')}", flush=True)

        script.on("message", on_message)
        script.load()
        scripts.append(script)

    print(f"watching {args.seconds:.0f}s", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        for script in scripts:
            try:
                script.unload()
            except Exception:
                pass
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
