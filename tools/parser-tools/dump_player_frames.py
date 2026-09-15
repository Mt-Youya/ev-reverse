"""Dump what the player's own H.264 decoder outputs, as images, to settle who is right.

Everything measurable so far agrees that the player feeds its decoder the same bytes we decrypt, so
the disagreement has to be downstream: either its decoder produces a real picture from those bytes
and ours does not, or its decoder produces the same flat grey and the picture on screen comes from a
different layer entirely. Those are very different problems, and the decoded AVFrame distinguishes
them directly.

`h264_decode_frame` (RVA 0xB9648) receives `(avctx, AVFrame *data, int *got_frame, AVPacket *pkt)`
and fills the frame on the way out, so the plane is read on leave. The layout was read off the live
struct rather than assumed -- AVFrame has `data[8]` at +0x00, `linesize[8]` at +0x40,
`extended_data` at +0x60, `width` at +0x68, `height` at +0x6C, `format` at +0x74 -- and on entry the
frame is still empty (width 0, format -1), which is why an earlier version that read it on entry
dumped nothing at all and looked like "the player is not decoding".

    python dump_player_frames.py --pid 10188 --seconds 60
"""

import argparse
import json
import os
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "decode_frames")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var calls = 0, dumps = 0, skipped = 0;
var PERIOD = 25;      // one frame per second of 25 fps video
var LIMIT = 10;

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
    var want = (calls % PERIOD) === 0 && dumps < LIMIT;
    calls += 1;
    this.dump = want;
    this.frame = args[1];
    this.pts = 0; this.size = -1;
    try {
      var pkt = args[3];
      if (!pkt.isNull()) {
        this.pts = pkt.add(0x08).readS64().toNumber();
        this.size = pkt.add(0x20).readS32();
      }
    } catch (e) {}
  },
  onLeave: function (retval) {
    if (!this.dump) return;
    this.dump = false;
    var meta = { at: Date.now() / 1000, call: calls, pts: this.pts, pkt_size: this.size,
                 retval: retval.toInt32() };
    try {
      var f = this.frame;
      meta.w = f.add(0x68).readS32();
      meta.h = f.add(0x6c).readS32();
      meta.fmt = f.add(0x74).readS32();
      meta.stride = f.add(0x40).readS32();
      meta.key_frame = f.add(0x78).readS32();
      var data = f.readPointer();
      meta.data = data.toString();
      if (meta.w < 16 || meta.w > 8192 || meta.h < 16 || meta.h > 8192 || data.isNull() ||
          Math.abs(meta.stride) < meta.w || meta.stride * meta.h > 64 * 1024 * 1024) {
        skipped += 1;
        meta.skipped = true;
        send({ t: 'frame', meta: meta });
        return;
      }
      meta.index = dumps;
      dumps += 1;
      send({ t: 'frame', meta: meta }, data.readByteArray(meta.stride * meta.h));
    } catch (e) {
      meta.error = String(e);
      send({ t: 'frame', meta: meta });
    }
  }
});

setInterval(function () { send({ t: 'stats', calls: calls, dumps: dumps, skipped: skipped }); }, 5000);
send({ t: 'armed', base: dll.base.toString() });
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, action="append", required=True)
    ap.add_argument("--seconds", type=float, default=60)
    args = ap.parse_args()

    if os.path.isdir(OUT):
        for name in os.listdir(OUT):
            os.remove(os.path.join(OUT, name))
    os.makedirs(OUT, exist_ok=True)
    log = open(os.path.join(OUT, "events.jsonl"), "w", encoding="utf-8")
    sessions = []

    for pid in args.pid:
        script = frida.attach(pid).create_script(JS)

        def on_message(message, data, pid=pid):
            if message.get("type") == "error":
                print(f"[pid {pid}] script error: {message.get('description')}", flush=True)
                return
            if message.get("type") != "send":
                return
            payload = message["payload"]
            if payload.get("t") == "armed":
                print(f"[pid {pid}] armed at {payload['base']}", flush=True)
            elif payload.get("t") == "key":
                log.write(json.dumps({"pid": pid, **payload}) + "\n")
                log.flush()
            elif payload.get("t") == "stats":
                print(f"[pid {pid}] calls={payload['calls']} dumps={payload['dumps']} "
                      f"skipped={payload['skipped']}", flush=True)
            elif payload.get("t") == "frame":
                meta = payload["meta"]
                log.write(json.dumps({"pid": pid, **meta}) + "\n")
                log.flush()
                if data is not None and not meta.get("skipped"):
                    name = f"p{pid}_{meta['index']:02d}_{meta['w']}x{meta['h']}_s{meta['stride']}.gray"
                    with open(os.path.join(OUT, name), "wb") as out:
                        out.write(bytes(data))
                    print(f"[pid {pid}] {name} {len(data):,} bytes fmt={meta['fmt']} "
                          f"key={meta['key_frame']}", flush=True)
                else:
                    print(f"[pid {pid}] frame meta only: {meta}", flush=True)

        script.on("message", on_message)
        script.load()
        sessions.append(script)

    print(f"watching {args.seconds:.0f}s", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        for script in sessions:
            try:
                script.unload()
            except Exception:
                pass
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
