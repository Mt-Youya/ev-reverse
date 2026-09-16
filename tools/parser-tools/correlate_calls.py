"""Decide whether level-40 streams are decoded by the software decoder, or only appear to be.

Everything measured so far says the bytes this project decrypts are the bytes the player's
`h264_decode_frame` receives, that its H.264 tables are stock, and that eight other decoder
implementations conceal a level-40 I frame completely -- while a picture is on screen when those
segments play. Two explanations are left, and they are told apart by *which call produces a frame*:

  * the software decoder does produce the picture from those bytes   -> its code path differs from
    stock, despite the tables matching, and that difference is the protection;
  * it produces nothing while level-40 parameter sets are arriving   -> the picture comes from
    somewhere else (a hardware decoder, whose own CABAC tables were the only stock copy found in the
    whole process) and this function is not what we should be reading.

So this records, per call, the parameter set's level and whether the call returned a usable frame,
and reports the two side by side.

    python correlate_calls.py --seconds 45
"""

import argparse
import subprocess
import sys
import time

import frida

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var calls = 0, levelCalls = 0, levelFrames = 0, otherFrames = 0;

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    this.frame = args[1];
    this.level = null; this.idr = false; this.pts = 0; this.size = 0;
    calls += 1;
    try {
      var pkt = args[3];
      if (pkt.isNull()) return;
      var size = pkt.add(0x20).readS32();
      var data = pkt.add(0x18).readPointer();
      this.pts = pkt.add(0x08).readS64().toNumber();
      this.size = size;
      if (size < 1024 || size > 8 * 1024 * 1024 || data.isNull()) return;
      var raw = new Uint8Array(data.readByteArray(Math.min(size, 8192)));
      for (var i = 0; i + 6 < raw.length; i++) {
        if (raw[i] === 0 && raw[i + 1] === 0 && raw[i + 2] === 1) {
          var type = raw[i + 3] & 0x1f;
          if (type === 7) { this.level = raw[i + 6]; }
          if (type === 5) { this.idr = true; }
        }
      }
    } catch (e) {}
  },
  onLeave: function (retval) {
    var w = 0, h = 0, stride = 0, data = null, mean = null, sd = null;
    try {
      var f = this.frame;
      w = f.add(0x68).readS32(); h = f.add(0x6c).readS32(); stride = f.add(0x40).readS32();
      data = f.readPointer();
      if (w >= 16 && h >= 16 && stride >= w && !data.isNull()) {
        var plane = new Uint8Array(data.readByteArray(Math.min(stride * h, 4 * 1024 * 1024)));
        var sum = 0, sum2 = 0, n = 0;
        for (var y = 0; y < h; y += 8) {
          for (var x = 0; x < w; x += 8) {
            var v = plane[y * stride + x];
            sum += v; sum2 += v * v; n++;
          }
        }
        mean = sum / n;
        sd = Math.sqrt(sum2 / n - mean * mean);
      }
    } catch (e) {}
    if (this.level !== null) {
      levelCalls += 1;
      if (mean !== null) levelFrames += 1;
    } else if (mean !== null) {
      otherFrames += 1;
    }
    if (this.level !== null || mean !== null) {
      send({ t: 'call', call: calls, pts: this.pts, size: this.size, level: this.level,
             idr: this.idr, retval: retval.toInt32(), w: w, h: h,
             mean: mean, sd: sd });
    }
  }
});

setInterval(function () {
  send({ t: 'stats', calls: calls, levelCalls: levelCalls, levelFrames: levelFrames,
         otherFrames: otherFrames });
}, 5000);
send({ t: 'armed', base: dll.base.toString() });
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
    ap.add_argument("--seconds", type=float, default=45)
    args = ap.parse_args()

    pid = args.pid or renderer_pid()
    if not pid:
        print("EVPlayer2 is not running")
        return 1
    print(f"watching pid {pid} for {args.seconds:.0f}s")

    levels = {}
    stats = {}
    script = frida.attach(pid).create_script(JS)

    def on_message(message, data):
        if message.get("type") == "error":
            print("script error:", message.get("description"))
            return
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "armed":
            print(f"armed at {payload['base']}")
        elif payload.get("t") == "stats":
            stats.update(payload)
        elif payload.get("t") == "call":
            level = payload["level"]
            if level is None:
                return
            record = levels.setdefault(level, {"calls": 0, "frames": 0, "sd": [], "ids": 0})
            record["calls"] += 1
            if payload["idr"]:
                record["ids"] += 1
            if payload["mean"] is not None:
                record["frames"] += 1
                record["sd"].append(round(payload["sd"], 1))

    script.on("message", on_message)
    script.load()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            script.unload()
        except Exception:
            pass

    print(f"\ncalls={stats.get('calls')}  calls carrying a parameter set="
          f"{stats.get('levelCalls')}  of those returning a frame={stats.get('levelFrames')}  "
          f"frames from calls without one={stats.get('otherFrames')}")
    if not levels:
        print("\nno call in this window carried an SPS: the player was not starting a segment")
        return 0
    for level in sorted(levels):
        record = levels[level]
        shares = record["sd"][:8]
        print(f"\nlevel {level}: {record['calls']} call(s) with a parameter set, "
              f"{record['ids']} of them an IDR, {record['frames']} returned a frame")
        if shares:
            flat = sum(1 for value in record["sd"] if value < 3)
            print(f"  frame spread samples {shares} ... {flat} of {len(record['sd'])} are flat grey")
    print("\nReading: frames on the same calls that carry a level-40 parameter set mean this decoder "
          "is the one producing the picture; none means the picture comes from elsewhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
