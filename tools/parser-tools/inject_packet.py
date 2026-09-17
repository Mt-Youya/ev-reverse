"""Hand the player's own decoder one of our level-40 packets and look at what comes back.

Every decoder available to us turns the level-40 segments into flat grey, while the player claims to
play the lesson they belong to. The clean way to separate "the decoder is patched" from "the segments
are broken at the source" is not to watch the player play one -- it is to put one of those packets in
front of the decoder it uses for everything else and read the frame that comes out.

The packet is an ordinary access unit: AUD, SPS, PPS, IDR, taken straight out of the file we decrypted.
It is copied into the target's memory once, then swapped into the AVPacket of a single decode call
(data pointer and size only). The frame that call returns is measured and written out, and the
injection disarms itself immediately so the player's own stream resumes on the next packet.

    python inject_packet.py --segment captured/classify/<file>.ts --seconds 40 --dump captured/inject
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

JS_TEMPLATE = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var payload = PAYLOAD_BYTES;
var injected = false, armed = true, calls = 0, injectedAt = -1;
var buffer = null;

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
    this.didInject = false;
    calls += 1;
    if (!armed || calls < 40) return;          // let the player settle first
    armed = false;
    try {
      var pkt = args[3];
      if (pkt.isNull()) return;
      var size = pkt.add(0x20).readS32();
      if (size < 512 || size > 4 * 1024 * 1024) { armed = true; return; }   // prefer a slice packet
      if (buffer === null) {
        buffer = Memory.alloc(payload.length);
        buffer.writeByteArray(payload);
      }
      this.savedData = pkt.add(0x18).readPointer();
      this.savedSize = size;
      pkt.add(0x18).writePointer(buffer);
      pkt.add(0x20).writeS32(payload.length);
      this.didInject = true;
      injected = true;
      injectedAt = calls;
      send({ t: 'injected', call: calls, replaced: size, with: payload.length,
             pts: pkt.add(0x08).readS64().toNumber() });
    } catch (e) {
      send({ t: 'error', message: String(e) });
    }
  },
  onLeave: function (retval) {
    if (!this.didInject) return;
    this.didInject = false;
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
        blob = data.readByteArray(stride * h);
      }
    } catch (e) { stats = { error: String(e) }; }
    send({ t: 'result', call: calls, retval: retval.toInt32(), stats: stats }, blob || undefined);
  }
});

setInterval(function () { send({ t: 'stats', calls: calls, injected: injected, at: injectedAt }); },
            3000);
send({ t: 'armed', base: dll.base.toString() });
"""


def build_payload(segment):
    from slice_probe import nals_of
    blob = open(segment, "rb").read()
    wanted = []
    for nal_type, body in nals_of(blob):
        if nal_type in (7, 8) and not any(t == nal_type for t, _ in wanted):
            wanted.append((nal_type, body))
        if nal_type == 5 and not any(t == 5 for t, _ in wanted):
            wanted.append((nal_type, body))
    order = {7: 0, 8: 1, 5: 2}
    wanted.sort(key=lambda item: order[item[0]])
    out = bytearray(b"\x00\x00\x00\x01\x09\xf0")          # access unit delimiter
    for _, body in wanted:
        out += b"\x00\x00\x00\x01" + body
    return bytes(out), [t for t, _ in wanted]


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
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    payload, kinds = build_payload(args.segment)
    print(f"payload: {len(payload):,} bytes, NAL types {kinds} from "
          f"{os.path.basename(args.segment)}")
    if not payload or 5 not in kinds:
        print("no IDR found -- nothing to inject")
        return 1
    if args.dump:
        os.makedirs(args.dump, exist_ok=True)

    js = JS_TEMPLATE.replace("PAYLOAD_BYTES", json.dumps(list(payload)))
    for pid in (args.pid or renderer_pids()[:1]):
        script = frida.attach(pid).create_script(js)

        def on_message(message, data, pid=pid):
            if message.get("type") == "error":
                print(f"[{pid}] script error: {message.get('description')}", flush=True)
                return
            if message.get("type") != "send":
                return
            payload_message = message["payload"]
            kind = payload_message.get("t")
            if kind == "armed":
                print(f"[{pid}] armed at {payload_message['base']}", flush=True)
            elif kind == "stats":
                print(f"[{pid}] calls={payload_message['calls']} "
                      f"injected={payload_message['injected']}", flush=True)
            elif kind == "error":
                print(f"[{pid}] {payload_message['message']}", flush=True)
            elif kind == "injected":
                print(f"[{pid}] injected at call {payload_message['call']}: replaced a "
                      f"{payload_message['replaced']:,} B packet with {payload_message['with']:,} B, "
                      f"pts={payload_message['pts']}", flush=True)
            elif kind == "result":
                stats = payload_message.get("stats") or {}
                print(f"[{pid}] decoder returned {payload_message['retval']}, frame "
                      f"{stats.get('w')}x{stats.get('h')} mean={stats.get('mean')} "
                      f"sd={stats.get('sd')}", flush=True)
                if stats.get("sd") is not None:
                    print(f"[{pid}] VERDICT: " + ("a picture -- the player's decoder handles this "
                                                  "packet" if stats["sd"] >= 3 else
                                                  "flat grey -- this packet is not decodable by the "
                                                  "player's decoder either"), flush=True)
                if args.dump and data is not None and stats.get("w"):
                    name = os.path.join(args.dump, os.path.basename(args.segment) + ".gray")
                    with open(name, "wb") as out:
                        out.write(bytes(data))
                    print(f"[{pid}] wrote {name}", flush=True)

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
