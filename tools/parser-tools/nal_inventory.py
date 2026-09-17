"""Inventory the NAL units that actually reach the decoder, packet by packet.

Everything the decoder reads comes out of the packets, and the packets are our bytes -- but that only
says *which* bytes, not *which parameter sets*. If the file's SPS and PPS never reach the decoder, or
reach it once and are later replaced by something the player supplies, then the stream being decoded is
not the stream we are decoding, and no amount of bitstream statistics will show it.

So: parse every packet on its way in, record the NAL types and sizes in order, and report the shape of
the stream as the decoder sees it -- including the parameter sets, in full, the first time they appear.

    python nal_inventory.py --seconds 30 --dump captured/nals
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
var calls = 0;

function inventory(pointer, size) {
  var raw = new Uint8Array(pointer.readByteArray(size));
  var out = [];
  var starts = [];
  for (var i = 0; i + 3 < size; i++) {
    if (raw[i] === 0 && raw[i + 1] === 0 && raw[i + 2] === 1) {
      starts.push(i + 3);
      i += 2;
    }
  }
  for (var k = 0; k < starts.length; k++) {
    var body = starts[k];
    var end = (k + 1 < starts.length) ? starts[k + 1] - 3 : size;
    if (end <= body) continue;
    out.push({ type: raw[body] & 0x1f, ref: (raw[body] >> 5) & 3, size: end - body, at: body });
  }
  return { nals: out, raw: raw };
}

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var pkt = args[3];
    if (pkt.isNull()) return;
    try {
      var size = pkt.add(0x20).readS32();
      var data = pkt.add(0x18).readPointer();
      var pts = pkt.add(0x08).readS64().toNumber();
      if (size <= 0 || size > 8 * 1024 * 1024 || data.isNull()) return;
      var info = inventory(data, size);
      calls += 1;
      var kinds = info.nals.map(function (n) { return n.type; });
      var interesting = kinds.indexOf(7) >= 0 || kinds.indexOf(8) >= 0;
      send({ t: 'packet', n: calls, pts: pts, size: size, kinds: kinds,
             sizes: info.nals.map(function (n) { return n.size; }), interesting: interesting });
      if (interesting) {
        // parameter sets are the thing under suspicion: keep them verbatim
        for (var k = 0; k < info.nals.length; k++) {
          var nal = info.nals[k];
          if (nal.type === 7 || nal.type === 8) {
            send({ t: 'params', n: calls, pts: pts, type: nal.type, size: nal.size,
                   at: nal.at }, data.add(nal.at).readByteArray(nal.size));
          }
        }
      }
    } catch (e) {
      send({ t: 'error', message: String(e) });
    }
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
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    if args.dump:
        os.makedirs(args.dump, exist_ok=True)
    log = open(os.path.join(args.dump or ".", "packets.jsonl"), "w", encoding="utf-8")
    shapes = {}
    params = 0

    for pid in (args.pid or renderer_pids()[:1]):
        script = frida.attach(pid).create_script(JS)

        def on_message(message, data, pid=pid):
            nonlocal params
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
                print(f"[{pid}] packets={payload['calls']}", flush=True)
            elif kind == "error":
                print(f"[{pid}] {payload['message']}", flush=True)
            elif kind == "packet":
                log.write(json.dumps(payload) + "\n")
                shape = tuple(payload["kinds"])
                shapes[shape] = shapes.get(shape, 0) + 1
            elif kind == "params":
                params += 1
                head = bytes(data)[:24].hex(" ") if data is not None else ""
                print(f"[{pid}] packet {payload['n']} pts={payload['pts']}: NAL type "
                      f"{payload['type']} of {payload['size']} B  head={head}", flush=True)
                if args.dump and data is not None:
                    name = os.path.join(args.dump,
                                        f"nal{payload['type']}_p{payload['n']}_{payload['size']}.bin")
                    with open(name, "wb") as out:
                        out.write(bytes(data))

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

    print(f"\n{params} parameter-set NAL(s) seen")
    print("packet shapes (NAL type sequence -> count):")
    for shape, count in sorted(shapes.items(), key=lambda kv: -kv[1])[:12]:
        kinds = collections_counter(shape)
        print(f"  {str(dict(kinds)):<46} x{count}")
    return 0


def collections_counter(shape):
    counts = {}
    for kind in shape:
        counts[kind] = counts.get(kind, 0) + 1
    return counts


if __name__ == "__main__":
    sys.exit(main())
