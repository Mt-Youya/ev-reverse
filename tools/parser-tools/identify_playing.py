"""Identify what the player is playing right now, and whether it is what we would decrypt.

Three questions, one run, all about the lesson that is on screen at this moment:

  * which file is it?            the key the player sets names it, via the sync-byte oracle
  * is its SPS the SPS the decoder was actually handed?   compare bytes, not statistics
  * does our decode of it show a picture?                 luma mean and spread of one frame

The second question is the one worth insisting on. "The packets are our bytes" was measured on one
lesson at one time; a parameter set that arrives from somewhere other than the file -- a correct SPS
supplied by the player, with a decoy left in the stream -- would explain identical packets, green
frames, and every bitstream statistic looking healthy all at once.

    python identify_playing.py --seconds 25
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

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "captured", "key_files.json")
DOWNLOADS = r"D:\Downloads\EVPlayer2Downloads"
WORK = os.path.join(HERE, "captured", "playing")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var keys = [], params = [];

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
    if (key && /^[0-9a-f]{32}$/.test(key)) {
      keys.push(key);
      send({ t: 'key', at: Date.now() / 1000, key: key });
    }
  }
});

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var pkt = args[3];
    if (pkt.isNull()) return;
    try {
      var size = pkt.add(0x20).readS32();
      var data = pkt.add(0x18).readPointer();
      if (size <= 0 || size > 8 * 1024 * 1024 || data.isNull()) return;
      var raw = new Uint8Array(data.readByteArray(size));
      var i = 0;
      while (i + 3 < size) {
        if (raw[i] === 0 && raw[i + 1] === 0 && raw[i + 2] === 1) {
          var body = i + 3, type = raw[body] & 0x1f;
          if ((type === 7 || type === 8) && params.length < 4) {
            var next = -1;
            for (var j = body + 1; j + 3 < size; j++) {
              if (raw[j] === 0 && raw[j + 1] === 0 && raw[j + 2] === 1) { next = j; break; }
            }
            var end = next < 0 ? size : next;
            var slice = raw.subarray(body, end);
            params.push({ type: type, size: slice.length });
            send({ t: 'params', type: type, size: slice.length, pts: pkt.add(0x08).readS64().toNumber() },
                 new Uint8Array(slice).buffer);
          }
          i = body;
        } else {
          i += 1;
        }
      }
    } catch (e) {}
  }
});

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


def extract_params(blob):
    out = {}
    i = 0
    while i + 3 < len(blob):
        if blob[i] == 0 and blob[i + 1] == 0 and blob[i + 2] == 1:
            body = i + 3
            nal_type = blob[body] & 0x1F
            end = len(blob)
            for j in range(body + 1, len(blob) - 3):
                if blob[j] == 0 and blob[j + 1] == 0 and blob[j + 2] == 1:
                    end = j
                    break
            if nal_type in (7, 8):
                out.setdefault(nal_type, blob[body:end])
            i = body
        else:
            i += 1
    return out


def frame_stats(path):
    with tempfile.TemporaryDirectory() as work:
        raw = os.path.join(work, "f.gray")
        subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "select=eq(n\\,30)",
                        "-vsync", "0", "-pix_fmt", "gray", "-f", "rawvideo", "-y", raw],
                       capture_output=True)
        if not os.path.exists(raw) or os.path.getsize(raw) < 1920 * 1080:
            return None
        plane = open(raw, "rb").read(1920 * 1080)
        return statistics.fmean(plane), statistics.pstdev(plane)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, action="append")
    ap.add_argument("--seconds", type=float, default=25)
    args = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)
    keys = []
    params = {}

    for pid in (args.pid or renderer_pids()[:1]):
        script = frida.attach(pid).create_script(JS)

        def on_message(message, data, pid=pid):
            if message.get("type") == "error":
                print(f"[{pid}] script error: {message.get('description')}", flush=True)
                return
            if message.get("type") != "send":
                return
            payload = message["payload"]
            if payload.get("t") == "armed":
                print(f"[{pid}] armed at {payload['base']}", flush=True)
            elif payload.get("t") == "key":
                keys.append(payload["key"])
            elif payload.get("t") == "params":
                blob = bytes(data)
                params.setdefault(payload["type"], blob)
                print(f"[{pid}] decoder received NAL {payload['type']} ({len(blob)} B): "
                      f"{blob.hex(' ')}", flush=True)

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

    sys.path.insert(0, HERE)
    from build_videos import decrypt, file_for_key, first_pts
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    names = [n for n in os.listdir(DOWNLOADS) if n.endswith(".ts")]
    print(f"\n{len(keys)} key(s) seen while watching")
    seen = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        name = cache.get(key)
        if name is None or not os.path.exists(os.path.join(DOWNLOADS, name)):
            name = file_for_key(key, names)
            cache[key] = name
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1)
        if not name:
            print(f"  key {key} matches no file")
            continue
        plain = decrypt(open(os.path.join(DOWNLOADS, name), "rb").read(), key, name)
        raw = os.path.join(WORK, name + ".ts")
        with open(raw, "wb") as out:
            out.write(plain)
        mine = extract_params(plain)
        print(f"\n  {name}  (pts {first_pts(plain)})")
        for kind in (7, 8):
            theirs = params.get(kind)
            ours = mine.get(kind)
            if theirs is None or ours is None:
                print(f"    NAL {kind}: decoder={theirs is not None} file={ours is not None}")
            elif ours == theirs:
                print(f"    NAL {kind}: IDENTICAL ({len(ours)} B)")
            else:
                first = next((i for i in range(min(len(ours), len(theirs)))
                              if ours[i] != theirs[i]), min(len(ours), len(theirs)))
                print(f"    NAL {kind}: DIFFERENT at byte {first}")
                print(f"      file   : {ours.hex(' ')}")
                print(f"      decoder: {theirs.hex(' ')}")
        stats = frame_stats(raw)
        if stats:
            print(f"    our decode of frame 30: mean={stats[0]:.1f} sd={stats[1]:.2f}"
                  + ("   (flat grey = failed)" if stats[1] < 3 else "   (a picture)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
