"""Play one of our decrypted segments through the player's own decoder, access unit by access unit.

Injecting a single IDR proves nothing: the decoder defers the frame, and the packets that follow would
be the player's own, referencing a picture that no longer exists. Injecting the *whole* segment does
prove something. If the player's decoder turns our level-40 bytes into the lesson's picture, then the
bytes are fine and its decoder is the thing that differs, which is a patch to find rather than a
cipher to break. If it produces the same flat grey, then those segments are broken at the source and
there is nothing to undo.

Each access unit replaces one packet for one call, and the player's own stream resumes when the list
runs out -- its next IDR is at most one segment away, so any damage is a few seconds of artefacts.

    python inject_segment.py --segment captured/classify/<file>.ts [--frames 260] [--dump dir]
"""

import argparse
import json
import os
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var units = UNITS;
var maxSize = MAXSIZE;
var armed = true, index = 0, injected = 0, calls = 0, frames = 0, kept = 0;
var buffer = null;

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    this.frame = args[1];
    this.didInject = false;
    calls += 1;
    if (!armed || injected >= units.length) return;
    try {
      var pkt = args[3];
      if (pkt.isNull()) return;
      if (buffer === null) buffer = Memory.alloc(maxSize);
      var unit = units[injected];
      buffer.writeByteArray(unit);
      pkt.add(0x18).writePointer(buffer);
      pkt.add(0x20).writeS32(unit.length);
      this.didInject = true;
      this.unitIndex = injected;
      injected += 1;
      send({ t: 'inject', n: injected, of: units.length, size: unit.length,
             call: calls });
    } catch (e) {
      send({ t: 'error', message: String(e) });
    }
  },
  onLeave: function (retval) {
    // Report every call that produced a picture, injected or not: the interesting question is not
    // only what our units decode to, but whether the player's own packets keep producing frames
    // across the same window. "The injected calls produced nothing" and "this decoder produced
    // nothing for ten seconds" are very different findings and only the second column separates them.
    var stats = null, blob = null;
    var injectedThisCall = this.didInject;
    this.didInject = false;
    try {
      var f = this.frame;
      var w = f.add(0x68).readS32(), h = f.add(0x6c).readS32(), stride = f.add(0x40).readS32();
      var data = f.readPointer();
      if (w >= 16 && h >= 16 && stride >= w && !data.isNull()) {
        var plane = new Uint8Array(data.readByteArray(stride * h));
        var sum = 0, sum2 = 0, n = 0;
        for (var y = 0; y < h; y++) {
          for (var x = 0; x < w; x++) {
            var v = plane[y * stride + x];
            sum += v; sum2 += v * v; n++;
          }
        }
        var mean = sum / n;
        stats = { mean: mean, sd: Math.sqrt(sum2 / n - mean * mean), w: w, h: h, stride: stride };
        frames += 1;
        if (kept < 12) {
          kept += 1;
          blob = data.readByteArray(stride * h);
        }
      }
    } catch (e) { stats = { error: String(e) }; }
    if (stats === null && !injectedThisCall) return;
    send({ t: 'frame', unit: this.unitIndex, call: calls, retval: retval.toInt32(),
           injected: injectedThisCall, stats: stats, save: blob !== null }, blob || undefined);
  }
});

setInterval(function () { send({ t: 'stats', calls: calls, injected: injected, frames: frames }); },
            3000);
send({ t: 'armed', base: dll.base.toString(), units: units.length, maxSize: maxSize });
"""


def access_units(segment):
    """Split the elementary stream into access units, the way the player's demuxer does."""
    from slice_probe import elementary_stream, nals_of
    blob = open(segment, "rb").read()
    es = elementary_stream(blob)
    units, current = [], bytearray()
    for nal_type, body in nals_of(es):
        if nal_type == 9 and current:                 # an access unit delimiter starts the next one
            units.append(bytes(current))
            current = bytearray()
        current += b"\x00\x00\x00\x01" + body
    if current:
        units.append(bytes(current))
    return [unit for unit in units if len(unit) > 8]


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
    ap.add_argument("--segment", required=True)
    ap.add_argument("--pid", type=int, action="append")
    ap.add_argument("--frames", type=int, default=0, help="stop injecting after this many units")
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    units = access_units(args.segment)
    if args.frames:
        units = units[:args.frames]
    max_size = max(len(unit) for unit in units)
    total = sum(len(unit) for unit in units)
    print(f"{len(units)} access unit(s), {total:,} bytes, largest {max_size:,} "
          f"from {os.path.basename(args.segment)}")
    if args.dump:
        os.makedirs(args.dump, exist_ok=True)

    js = (JS.replace("UNITS", json.dumps([list(unit) for unit in units]))
            .replace("MAXSIZE", str(max_size + 64)))
    for pid in (args.pid or renderer_pids()[:1]):
        script = frida.attach(pid).create_script(js)

        def on_message(message, data, pid=pid):
            if message.get("type") == "error":
                print(f"[{pid}] script error: {message.get('description')}", flush=True)
                return
            if message.get("type") != "send":
                return
            payload = message["payload"]
            kind = payload.get("t")
            if kind == "armed":
                print(f"[{pid}] armed at {payload['base']} -- {payload['units']} unit(s) ready",
                      flush=True)
            elif kind == "stats":
                print(f"[{pid}] calls={payload['calls']} injected={payload['injected']} "
                      f"frames={payload['frames']}", flush=True)
            elif kind == "error":
                print(f"[{pid}] {payload['message']}", flush=True)
            elif kind == "frame":
                stats = payload.get("stats") or {}
                sd = stats.get("sd")
                note = "(no frame)"
                if sd is not None:
                    note = (f"mean={stats['mean']:6.1f} sd={sd:6.2f} "
                            + ("FLAT GREY" if sd < 3 else "picture"))
                tag = "injected" if payload.get("injected") else "own     "
                print(f"[{pid}] {tag} unit {payload['unit'] + 1:4d}  ret={payload['retval']:>7}  "
                      f"{note}", flush=True)
                if args.dump and data is not None and stats.get("w"):
                    name = os.path.join(args.dump, f"unit_{payload['unit'] + 1:04d}.gray")
                    with open(name, "wb") as out:
                        out.write(bytes(data))
                    meta = dict(stats)
                    meta["unit"] = payload["unit"] + 1
                    with open(os.path.join(args.dump, "frames.jsonl"), "a", encoding="utf-8") as out:
                        out.write(json.dumps(meta) + "\n")

        script.on("message", on_message)
        script.load()
        print(f"watching {args.seconds:.0f}s", flush=True)
        try:
            time.sleep(args.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                script.unload()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
