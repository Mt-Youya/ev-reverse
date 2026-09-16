"""Say what the player is playing right now: lesson, position, protection class, decodability.

Nobody should have to remember which lesson is on screen, and the lesson can change between two
measurements. So this asks the player instead of a person: it reads the segment keys the player sets
and the SPS level of the packets it feeds its decoder, maps the keys back to files on disk, decrypts
those files, and reports the lesson prefix, the playback position, whether the stream is one of the
level-40 (blocked) ones or a stream we can decode, and whether the player is being handed the same
parameter sets the file carries.

Read-only: two hooks, no writes into the target, nothing that can take the player down with it.

    python whats_playing.py                 # 15 seconds, enough for a key and an IDR
    python whats_playing.py --seconds 40 --log
"""

import argparse
import json
import os
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = r"D:\Downloads\EVPlayer2Downloads"
CACHE = os.path.join(HERE, "captured", "key_files.json")
LOG = os.path.join(HERE, "captured", "playing_log.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var seen = {};

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
    if (key && /^[0-9a-f]{32}$/.test(key) && !seen[key]) {
      seen[key] = true;
      send({ t: 'key', at: Date.now() / 1000, key: key });
    }
  }
});

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var pkt = args[3];
    this.frame = args[1];
    this.pts = 0; this.size = 0; this.level = null;
    try {
      if (pkt.isNull()) return;
      var size = pkt.add(0x20).readS32();
      var data = pkt.add(0x18).readPointer();
      this.pts = pkt.add(0x08).readS64().toNumber();
      this.size = size;
      if (size < 8 || size > 8 * 1024 * 1024 || data.isNull()) return;
      if (size < 1024) return;                      // parameter sets never ride in the tiny slices
      var raw = new Uint8Array(data.readByteArray(Math.min(size, 4096)));
      for (var i = 0; i + 4 < raw.length; i++) {
        if (raw[i] === 0 && raw[i + 1] === 0 && raw[i + 2] === 1 && (raw[i + 3] & 0x1f) === 7) {
          // start code (3) + NAL header (1) + profile (1) + constraints (1) = level at +6
          this.level = raw[i + 6];
          break;
        }
      }
    } catch (e) {}
  },
  onLeave: function () {
    if (this.level === null) return;
    var stats = null;
    try {
      var f = this.frame;
      var w = f.add(0x68).readS32(), h = f.add(0x6c).readS32(), stride = f.add(0x40).readS32();
      var data = f.readPointer();
      if (w >= 16 && h >= 16 && stride >= w && !data.isNull()) {
        var plane = new Uint8Array(data.readByteArray(stride * h));
        var sum = 0, sum2 = 0, n = 0;
        for (var y = 0; y < h; y += 4) {
          for (var x = 0; x < w; x += 4) {
            var v = plane[y * stride + x];
            sum += v; sum2 += v * v; n++;
          }
        }
        var mean = sum / n;
        stats = { mean: mean, sd: Math.sqrt(sum2 / n - mean * mean), w: w, h: h };
      }
    } catch (e) {}
    send({ t: 'params', pts: this.pts, size: this.size, level: this.level, stats: stats });
  }
});

send({ t: 'armed', base: dll.base.toString() });
"""


def renderer_pids(hint):
    if hint:
        return [hint]
    listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True).stdout
    out = []
    for row in listing.splitlines():
        cells = [c.strip('"') for c in row.split('","')]
        if len(cells) >= 5 and cells[0] == "EVPlayer2.exe":
            out.append((int(cells[4].replace(",", "").replace(" K", "")) * 1024, int(cells[1])))
    return [pid for _, pid in sorted(out, reverse=True)]


def sps_level(blob):
    """Level byte of the stream's SPS. The file is a transport stream, so the SPS has to be pulled out
    of its PES payload first -- searching the raw file for a start code finds packed video data and
    returns a byte that is not a level at all."""
    from slice_probe import nals_of
    for nal_type, body in nals_of(blob):
        if nal_type == 7 and len(body) > 3:
            return body[3]
    return None


def first_pts(stream):
    for offset in range(0, len(stream) - 187, 188):
        packet = stream[offset:offset + 188]
        if packet[0] != 0x47 or not packet[1] & 0x40:
            continue
        control = (packet[3] >> 4) & 0x03
        if control in (0, 2):
            continue
        start = 4 + (1 + packet[4] if control == 3 else 0)
        payload = packet[start:]
        if payload[:3] != b"\x00\x00\x01" or not 0xE0 <= payload[3] <= 0xEF:
            continue
        if not payload[7] & 0x80:
            continue
        stamp = payload[9:14]
        if len(stamp) < 5:
            continue
        return (((stamp[0] >> 1) & 0x07) << 30) | (stamp[1] << 22) \
            | (((stamp[2] >> 1) & 0x7F) << 15) | (stamp[3] << 7) | ((stamp[4] >> 1) & 0x7F)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--log", action="store_true")
    args = ap.parse_args()

    pids = renderer_pids(args.pid)
    if not pids:
        print("EVPlayer2 is not running")
        return 1
    pid = pids[0]
    print(f"watching pid {pid} for {args.seconds:.0f}s")

    keys, packets = [], []
    script = frida.attach(pid).create_script(JS)

    def on_message(message, data):
        if message.get("type") == "error":
            print("script error:", message.get("description"))
            return
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "key":
            keys.append(payload["key"])
        elif payload.get("t") == "params":
            packets.append(payload)

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

    print(f"{len(keys)} key(s), {len(packets)} packet(s) carrying a parameter set")
    if not keys:
        print("no keys seen: the player is not downloading/playing a protected stream right now")
        return 0

    sys.path.insert(0, HERE)
    from build_videos import decrypt, file_for_key
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    names = [n for n in os.listdir(DOWNLOADS) if n.endswith(".ts")]

    levels_seen = [p["level"] for p in packets if p.get("level") is not None]
    player_stats = [p["stats"] for p in packets if p.get("stats")]

    print("\nwhat the player's own decoder did in this window:")
    if not player_stats:
        print("  (no readable output frame: either the decode produced nothing or the picture is not "
              "where this hook expects it)")
    for stats in player_stats[:5]:
        verdict = "FLAT GREY" if stats["sd"] < 3 else "a picture"
        print(f"  {stats['w']}x{stats['h']}  mean={stats['mean']:6.1f} sd={stats['sd']:6.2f}  {verdict}")
    if levels_seen:
        print(f"  SPS level(s) handed to the decoder: {sorted(set(levels_seen))}")
    for key in keys:
        name = cache.get(key)
        if name is None or not os.path.exists(os.path.join(DOWNLOADS, name)):
            name = file_for_key(key, names)
            cache[key] = name
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1)
        if not name:
            print(f"  key {key} matches no file on disk")
            continue
        plain = decrypt(open(os.path.join(DOWNLOADS, name), "rb").read(), key, name)
        pts = first_pts(plain)
        level = sps_level(plain)
        where = (pts or 0) / 90000
        lesson = name.split("-")[0]
        blocked = level == 40
        print(f"\n  file   : {name}")
        print(f"  lesson : {lesson}   position: {int(where) // 60}:{int(where) % 60:02d} "
              f"(pts {pts})")
        print(f"  SPS level {level} -> " + ("level 40: our decoders produce flat grey for this class"
                                           if blocked else
                                           "a class our decoders handle"))
        if args.log:
            with open(LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "at": time.time(), "pid": pid, "key": key, "file": name, "lesson": lesson,
                    "pts": pts, "seconds": int(where), "file_level": level,
                    "player_levels": sorted(set(levels_seen)),
                    "player_frame": player_stats[0] if player_stats else None,
                }) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
