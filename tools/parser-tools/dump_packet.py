"""Take the packet the player's decoder is handed and decode it here, with no file in between.

Every comparison so far went through our own decryption: we decrypted a file, and separately showed
that packets the player fed its decoder contained the same bytes. Both halves were measured, but never
in one step -- and the conclusion they support is strange enough (the same bytes decode to a picture
in one decoder and to nothing in eight others) that it deserves the direct form of the test: capture
the packet at the moment it is handed over, write it out untouched, and decode that.

If our decoder turns the player's own packet into a picture, then the bytes we hold are not the bytes
the player has, and every earlier comparison was measuring the wrong pair. If it comes out flat, the
bytes are identical and the difference is inside the decoder's code.

    python dump_packet.py --seconds 45 --out captured/player_packets
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

import frida

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var kept = 0, seen = 0;
var LIMIT = 6;

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    if (kept >= LIMIT) return;
    var pkt = args[3];
    if (pkt.isNull()) return;
    try {
      var size = pkt.add(0x20).readS32();
      var data = pkt.add(0x18).readPointer();
      if (size < 32768 || size > 4 * 1024 * 1024 || data.isNull()) return;
      var head = new Uint8Array(data.readByteArray(Math.min(size, 64)));
      // Only packets that carry a parameter set: those are the ones whose decoding we disagree about.
      var level = null, hasSps = false;
      for (var i = 0; i + 6 < head.length; i++) {
        if (head[i] === 0 && head[i + 1] === 0 && head[i + 2] === 1 && (head[i + 3] & 0x1f) === 7) {
          hasSps = true; level = head[i + 6];
        }
      }
      seen += 1;
      if (!hasSps) return;
      kept += 1;
      send({ t: 'packet', n: kept, size: size, level: level,
             pts: pkt.add(0x08).readS64().toNumber() }, data.readByteArray(size));
    } catch (e) {}
  }
});

setInterval(function () { send({ t: 'stats', kept: kept, seen: seen }); }, 5000);
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


def decode(path):
    with tempfile.TemporaryDirectory() as work:
        raw = os.path.join(work, "f.gray")
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "select=eq(n\\,0)",
                               "-vsync", "0", "-pix_fmt", "gray", "-f", "rawvideo", "-y", raw],
                              capture_output=True, text=True)
        errors = [line for line in (done.stderr or "").splitlines() if line.strip()]
        if not os.path.exists(raw) or os.path.getsize(raw) < 1920 * 1080:
            return None, errors
        plane = open(raw, "rb").read(1920 * 1080)
        return (statistics.fmean(plane), statistics.pstdev(plane)), errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=45)
    ap.add_argument("--out", default=os.path.join("captured", "player_packets"))
    args = ap.parse_args()

    pid = args.pid or renderer_pid()
    if not pid:
        print("EVPlayer2 is not running")
        return 1
    os.makedirs(args.out, exist_ok=True)
    print(f"capturing packets from pid {pid} for {args.seconds:.0f}s")

    saved = []
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
            print(f"  {payload['seen']} large packet(s) seen, {payload['kept']} with a parameter set")
        elif payload.get("t") == "packet" and data is not None:
            name = os.path.join(args.out, f"packet_{payload['n']}_lvl{payload['level']}"
                                          f"_{payload['size']}.h264")
            with open(name, "wb") as handle:
                handle.write(bytes(data))
            saved.append((name, payload))
            print(f"  saved {os.path.basename(name)} (level {payload['level']})")

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

    if not saved:
        print("\nno packet with a parameter set was seen; nothing to decode")
        return 0
    print(f"\n{'packet':<28}{'level':>6}{'mean':>8}{'sd':>8}  verdict")
    for name, payload in saved:
        stats, errors = decode(name)
        if stats is None:
            print(f"{os.path.basename(name):<28}{payload['level']:>6}{'':>8}{'':>8}  no frame "
                  f"({len(errors)} error line(s))")
        else:
            verdict = "FLAT GREY" if stats[1] < 3 else "a picture"
            print(f"{os.path.basename(name):<28}{payload['level']:>6}{stats[0]:>8.1f}{stats[1]:>8.2f}"
                  f"  {verdict}")
    print("\nA picture here would mean the player's packet is not what our files contain; flat means "
          "the bytes match and the difference is in the decoder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
