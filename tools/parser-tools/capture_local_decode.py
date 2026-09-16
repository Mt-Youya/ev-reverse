"""Capture keys, complete decoder packets and output frames in one local player session.

    python capture_local_decode.py --pid <renderer PID> --seconds 120

Outputs use a fresh directory under captured/local_decode; existing evidence is preserved.
Frame PTS is read from the returned AVFrame, independently of the current input packet PTS.
Offsets apply to the verified PlayerLibRender56_vs.dll (Lavc58.54.100).
"""

import argparse
import datetime
import json
from pathlib import Path
import sys
import time

import frida


JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var calls = 0, frames = 0, dumps = 0, keys = 0;
function stdBytes(p) {
  var size = p.add(0x10).readU64().toNumber();
  var cap = p.add(0x18).readU64().toNumber();
  if (size <= 0 || size > 4 * 1024 * 1024 || cap < size) return null;
  return (cap < 16 ? p : p.readPointer()).readByteArray(size);
}
Interceptor.attach(dll.base.add(0x1FD60), {
  onEnter: function (args) {
    var input = args[1].readUtf8String();
    if (input && (input.indexOf('.ts') >= 0 || input.indexOf('evs_playkey=') >= 0))
      send({t: 'kdf', at: Date.now() / 1000, input: input});
  }
});
Interceptor.attach(dll.base.add(0x1EA10), {
  onEnter: function (args) {
    this.outputString = args[0];
    this.caller = this.returnAddress.sub(dll.base).toString();
  },
  onLeave: function () {
    var data = stdBytes(this.outputString);
    if (data) send({t: 'payload', at: Date.now() / 1000, caller: this.caller}, data);
  }
});
var h2 = Process.getModuleByName('nghttp2.dll').findExportByName('nghttp2_submit_request');
if (h2) Interceptor.attach(h2, {
  onEnter: function (args) {
    var headers = {};
    for (var i = 0; i < Math.min(args[3].toInt32(), 48); i++) {
      var nv = args[2].add(i * 40);
      var nl = nv.add(16).readU64().toNumber(), vl = nv.add(24).readU64().toNumber();
      if (nl > 8192 || vl > 65536) continue;
      var name = nv.readPointer().readUtf8String(nl);
      if (name === 'authorization' || name.charAt(0) === ':')
        headers[name] = nv.add(8).readPointer().readUtf8String(vl);
    }
    send({t: 'request', at: Date.now() / 1000, headers: headers});
  }
});
Interceptor.attach(dll.base.add(0x40AF0), {
  onEnter: function (args) {
    var p = args[1];
    var size = p.add(0x10).readU64().toNumber();
    var cap = p.add(0x18).readU64().toNumber();
    if (size !== 32) return;
    var key = (cap < 16 ? p : p.readPointer()).readUtf8String(size);
    if (/^[0-9a-f]{32}$/.test(key)) {
      keys++;
      send({t: 'key', at: Date.now() / 1000, key: key});
    }
  }
});
Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    calls++;
    this.call = calls;
    this.frame = args[1];
    this.got = args[2];
    this.avctx = args[0].toString();
    var pkt = args[3];
    this.packetPts = null;
    if (pkt.isNull()) return;
    var size = pkt.add(0x20).readS32();
    var data = pkt.add(0x18).readPointer();
    this.packetPts = pkt.add(0x08).readS64().toString();
    if (size > 0 && size <= 4 * 1024 * 1024 && !data.isNull()) {
      send({t: 'packet', at: Date.now() / 1000, call: this.call, context: this.avctx,
            thread: this.threadId, size: size, pts: this.packetPts,
            dts: pkt.add(0x10).readS64().toString()}, data.readByteArray(size));
    }
  },
  onLeave: function (retval) {
    if (retval.toInt32() < 0 || this.got.isNull() || this.got.readS32() === 0) return;
    frames++;
    if ((frames - 1) % 75 !== 0 || dumps >= 30) return;
    var f = this.frame;
    var meta = {t: 'frame', at: Date.now() / 1000, call: this.call, context: this.avctx,
                decoded: frames, pts: f.add(0x88).readS64().toString(), packet_pts: this.packetPts,
                w: f.add(0x68).readS32(), h: f.add(0x6c).readS32(),
                fmt: f.add(0x74).readS32(), stride: f.add(0x40).readS32()};
    var data = f.readPointer();
    if (meta.fmt !== 0 || meta.w < 16 || meta.w > 8192 || meta.h < 16 || meta.h > 8192 ||
        meta.stride < meta.w || meta.stride * meta.h > 64 * 1024 * 1024 || data.isNull()) {
      meta.skipped = true;
      send(meta);
      return;
    }
    meta.index = dumps++;
    send(meta, data.readByteArray(meta.stride * meta.h));
  }
});
setInterval(function () { send({t: 'stats', calls: calls, frames: frames, dumps: dumps, keys: keys}); }, 5000);
send({t: 'armed', base: dll.base.toString()});
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--seconds", type=float, default=120)
    args = ap.parse_args()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out = Path(__file__).resolve().parent / "captured" / "local_decode" / stamp
    out.mkdir(parents=True)
    print(f"capture directory: {out}", flush=True)
    session = frida.attach(args.pid)
    script = session.create_script(JS)
    payloads = 0
    with (out / "events.jsonl").open("w", encoding="utf-8") as log:
        def on_message(message, data):
            nonlocal payloads
            if message.get("type") != "send":
                print(f"Frida: {message.get('description', message)}", flush=True)
                return
            event = {"pid": args.pid, **message["payload"]}
            kind = event["t"]
            if data is not None:
                if kind == "packet":
                    name = f"{event['call']:06d}_{event['size']}.bin"
                elif kind == "frame":
                    name = f"frame_{event['index']:02d}_{event['w']}x{event['h']}_s{event['stride']}.gray"
                else:
                    payloads += 1
                    name = f"payload_{payloads:04d}.payload"
                (out / name).write_bytes(data)
                event["file"] = name
            log.write(json.dumps(event) + "\n")
            log.flush()
            if kind == "key":
                with (out / "player_keys.jsonl").open("a", encoding="utf-8") as key_log:
                    key_log.write(json.dumps(event) + "\n")
            if kind in ("armed", "stats", "frame"):
                print(json.dumps(event), flush=True)
        script.on("message", on_message)
        try:
            script.load()
            time.sleep(args.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
