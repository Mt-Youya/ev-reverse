"""Watch one segment decode: what the parameter sets say, and whether a picture comes out.

Two classes of segment exist in this material and they are separable by the SPS level byte: the ones
at level 51 decode into a crisp screen recording, and the ones at level 40 come out flat grey from
every decoder on this machine. The question this answers is what the *player* does with a level-40
segment, which is the difference between two very different problems:

  * it feeds the decoder the same SPS the file carries, and gets a picture  -> the decoder is patched
  * it feeds the decoder a *different* SPS and gets a picture               -> the parameter sets are
                                                                              the layer, and the file's
                                                                              SPS is a decoy
  * it feeds the same SPS and gets the same flat grey                       -> the segments are broken
                                                                              at the source, and there
                                                                              is nothing to undo

So both sides are recorded for the same call: the SPS/PPS leaving the packet, and the luma statistics
of the AVFrame coming back. The frames themselves are written out the first time each level appears.

    python watch_segment.py --seconds 180 --dump captured/watch
"""

import argparse
import json
import os
import subprocess
import sys
import time

import frida

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var calls = 0, saved = 0, lastLevel = null, lastReport = 0;

function sstring(p) {
  if (!p || p.isNull()) return null;
  try {
    var size = p.add(0x10).readU64().toNumber();
    var cap = p.add(0x18).readU64().toNumber();
    if (size > 0 && size < 512 && cap >= size) {
      var data = (cap < 16) ? p.readByteArray(size) : p.readPointer().readByteArray(size);
      if (!data) return null;
      var bytes = new Uint8Array(data), out = '';
      for (var i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
      return out;
    }
  } catch (e) {}
  try { return p.readUtf8String(128); } catch (e) { return null; }
}

Interceptor.attach(dll.base.add(0x40AF0), {
  onEnter: function (args) {
    var key = sstring(args[1]);
    if (key && /^[0-9a-f]{32}$/.test(key)) send({ t: 'key', at: Date.now() / 1000, key: key });
  }
});

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    this.frame = args[1];
    this.level = null; this.sps = null; this.pps = null;
    this.pts = 0; this.size = 0; this.idr = false;
    calls += 1;
    try {
      var pkt = args[3];
      if (pkt.isNull()) return;
      var size = pkt.add(0x20).readS32();
      var data = pkt.add(0x18).readPointer();
      this.pts = pkt.add(0x08).readS64().toNumber();
      this.size = size;
      if (size <= 0 || size > 8 * 1024 * 1024 || data.isNull()) return;
      if (size < 4096) return;                 // parameter sets never ride in the tiny slices
      var raw = new Uint8Array(data.readByteArray(size));
      var i = 0;
      while (i + 4 < size) {
        if (raw[i] === 0 && raw[i + 1] === 0 && raw[i + 2] === 1) {
          var body = i + 3, type = raw[body] & 0x1f;
          if (type === 5) this.idr = true;
          if (type === 7 || type === 8) {
            var next = size;
            for (var j = body + 1; j + 3 < size; j++) {
              if (raw[j] === 0 && raw[j + 1] === 0 && raw[j + 2] === 1) { next = j; break; }
            }
            var slice = raw.subarray(body, next);
            if (type === 7) { this.level = slice[3]; this.sps = slice; }
            else { this.pps = slice; }
          }
          i = body;
        } else {
          i += 1;
        }
      }
    } catch (e) {}
  },
  onLeave: function (retval) {
    // Report when something changed, or once a second, and only when the call actually produced a
    // picture: the player decodes plenty of calls that return no frame at all (retval == size with an
    // empty AVFrame), and treating those as "grey" would be reading the absence of a frame as a
    // failed decode.
    var stats = null, blob = null;
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
      }
    } catch (e) { stats = { error: String(e) }; }

    var now = Date.now() / 1000;
    var changed = this.level !== null && this.level !== lastLevel;
    var due = stats !== null && (now - lastReport >= 1);
    var keep = false;
    if (stats !== null && (changed || saved < 8)) {
      keep = true;
      saved += 1;
    }
    if (this.level !== null) lastLevel = this.level;
    if (!changed && !due && !keep) return;
    lastReport = now;
    var out = { t: 'frame', call: calls, pts: this.pts, size: this.size, level: this.level,
                idr: this.idr, stats: stats, save: keep, retval: retval.toInt32() };
    if (this.sps) {
      out.sps = Array.prototype.map.call(this.sps, function (b) {
        return ('0' + b.toString(16)).slice(-2); }).join(' ');
    }
    if (keep) {
      var f2 = this.frame;
      var w2 = f2.add(0x68).readS32(), h2 = f2.add(0x6c).readS32(), s2 = f2.add(0x40).readS32();
      blob = f2.readPointer().readByteArray(s2 * h2);
    }
    send(out, blob || undefined);
  }
});

setInterval(function () { send({ t: 'stats', calls: calls }); }, 5000);
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
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    if args.dump:
        os.makedirs(args.dump, exist_ok=True)
    log = open(os.path.join(args.dump or ".", "watch.jsonl"), "w", encoding="utf-8")

    for pid in (args.pid or renderer_pids()[:1]):
        script = frida.attach(pid).create_script(JS)

        def on_message(message, data, pid=pid):
            if message.get("type") == "error":
                print(f"[{pid}] script error: {message.get('description')}", flush=True)
                return
            if message.get("type") != "send":
                return
            payload = message["payload"]
            kind = payload.get("t")
            if kind == "armed":
                print(f"[{pid}] armed at {payload['base']}", flush=True)
            elif kind == "stats":
                print(f"[{pid}] decode calls={payload['calls']}", flush=True)
            elif kind == "key":
                log.write(json.dumps(payload) + "\n")
                log.flush()
            elif kind == "frame":
                log.write(json.dumps(payload) + "\n")
                log.flush()
                stats = payload.get("stats") or {}
                note = ""
                if stats.get("sd") is not None:
                    note = f"mean={stats['mean']:6.1f} sd={stats['sd']:6.2f}"
                    note += "  FLAT GREY" if stats["sd"] < 3 else "  picture"
                print(f"[{pid}] pts={payload['pts'] / 90000:8.1f}s size={payload['size']:>7,} "
                      f"level={payload['level']} ret={payload['retval']:>3}  {note}", flush=True)
                if payload.get("save") and args.dump and data is not None and stats.get("w"):
                    name = os.path.join(args.dump,
                                        f"frame_lvl{payload['level']}_pts{payload['pts']}.gray")
                    with open(name, "wb") as out:
                        out.write(bytes(data))
                    print(f"[{pid}]   saved {os.path.basename(name)} "
                          f"({stats['w']}x{stats['h']} stride {stats['stride']})", flush=True)
                if payload.get("sps"):
                    print(f"[{pid}]   SPS {payload['sps']}", flush=True)

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
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
