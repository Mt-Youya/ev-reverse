"""Find the buffer where the decoder keeps the slice data, and see whether it is ever plaintext.

The grey files are not a decoder-version problem: FFmpeg 8.1 and 4.2.2 both conceal every macroblock
of the I frame. The bytes handed to `h264_decode_frame` are ours and the packet buffer is not
rewritten, so if a second layer is undone it happens on a copy.

That copy is findable without knowing any struct offsets. Take a 48-byte window of the packet that
contains no emulation-prevention sequence, so the window is byte-identical in the escaped and
unescaped forms, then search process memory for it. Addresses holding it while the call is in flight
are candidates; re-reading them when the call returns shows which were rewritten, and with `--dump`
the whole buffer on both sides of the transform lands on disk, which is what makes the transform
reproducible rather than merely observable.

    python find_plaintext.py --seconds 45 --dump captured\transform
"""

import argparse
import os
import subprocess
import sys
import time

import frida

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var BIG = 32768;                       // only IDR-sized packets are worth this
var WINDOW = 48;
var MAX_ADDRESSES = 4;
var state = { scanned: 0, hits: 0, changed: 0 };

function hex(bytes) {
  var out = [];
  for (var i = 0; i < bytes.length; i++) out.push(('0' + bytes[i].toString(16)).slice(-2));
  return out.join(' ');
}

function escapeFreeWindow(raw, size) {
  var start = Math.floor(size * 0.3);
  for (var i = start; i + WINDOW + 2 < size; i++) {
    var ok = true;
    for (var j = 0; j < WINDOW; j++) {
      if (raw[i + j] === 0 && raw[i + j + 1] === 0 && raw[i + j + 2] <= 3) { ok = false; break; }
    }
    if (ok) return i;
  }
  return -1;
}

function readWhole(address, size) {
  var length = size;
  while (length > 4096) {
    try { return address.readByteArray(length); } catch (e) { length = Math.floor(length / 2); }
  }
  try { return address.readByteArray(length); } catch (e) { return null; }
}

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    this.pkt = args[3];
    this.armed = false;
    if (this.pkt.isNull()) return;
    var size, data;
    try {
      size = this.pkt.add(0x20).readS32();
      data = this.pkt.add(0x18).readPointer();
    } catch (e) { return; }
    if (size < BIG || size > 4 * 1024 * 1024 || data.isNull()) return;
    try {
      var raw = new Uint8Array(data.readByteArray(size));
      var offset = escapeFreeWindow(raw, size);
      if (offset < 0) return;
      var needle = hex(raw.subarray(offset, offset + WINDOW));
      var hits = [];
      var ranges = Process.enumerateRanges('r--');
      for (var r = 0; r < ranges.length && hits.length < MAX_ADDRESSES; r++) {
        var found;
        try { found = Memory.scanSync(ranges[r].base, ranges[r].size, needle); } catch (e) { continue; }
        for (var k = 0; k < found.length && hits.length < MAX_ADDRESSES; k++) {
          hits.push(found[k].address);
        }
      }
      state.scanned += 1;
      state.hits += hits.length;
      this.armed = true;
      this.data = data;
      this.size = size;
      this.offset = offset;
      this.pktHex = data.toString();
      this.needle = needle;
      this.hits = hits;
      this.before = [];
      for (var i = 0; i < hits.length; i++) {
        this.before.push(readWhole(hits[i], size));
      }
      send({ t: 'scan', size: size, offset: offset, pkt: data.toString(),
             hits: hits.map(function (a, i) {
               return { address: a.toString(), bytes: this.before[i] ? this.before[i].byteLength : 0 };
             }, this) });
    } catch (e) {
      send({ t: 'scan_error', message: String(e) });
    }
  },
  onLeave: function (retval) {
    if (!this.armed) return;
    this.armed = false;
    for (var i = 0; i < this.hits.length; i++) {
      var address = this.hits[i];
      var after = readWhole(address, this.size);
      var head = null, untouched = null;
      try {
        head = hex(new Uint8Array(address.readByteArray(WINDOW)));
        untouched = head === this.needle;
      } catch (e) {}
      if (!untouched) state.changed += 1;
      send({ t: 'after', address: address.toString(), size: this.size,
             head: head, untouched: untouched, after_bytes: after ? after.byteLength : 0 },
           after || undefined);
      if (this.before[i]) {
        send({ t: 'before', address: address.toString(), size: this.size },
             this.before[i]);
      }
    }
    send({ t: 'pkt', size: this.size, pkt: this.data.toString() },
         this.data.readByteArray(this.size));
  }
});

setInterval(function () { send({ t: 'stats', scanned: state.scanned, hits: state.hits,
                                 changed: state.changed }); }, 5000);
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
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    if args.dump:
        os.makedirs(args.dump, exist_ok=True)
    saved = {"packets": 0}

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
                print(f"[{pid}] big packets={payload['scanned']} hits={payload['hits']} "
                      f"changed={payload['changed']}", flush=True)
            elif kind == "scan":
                print(f"\n[{pid}] packet {payload['size']:,} B at {payload['pkt']}, window +"
                      f"{payload['offset']}:", flush=True)
                for row in payload["hits"]:
                    print(f"    {row['address']}  {row['bytes']:,} bytes readable", flush=True)
            elif kind == "scan_error":
                print(f"[{pid}] scan failed: {payload['message']}", flush=True)
            elif kind == "after":
                mark = "unchanged" if payload["untouched"] else "CHANGED"
                print(f"[{pid}] {payload['address']}  {mark}  {payload['after_bytes']:,} bytes  "
                      f"head={payload['head']}", flush=True)
                if args.dump and data is not None and not payload["untouched"]:
                    saved["packets"] += 1
                    tag = payload["address"].replace("0x", "")
                    with open(os.path.join(args.dump, f"after_{tag}.bin"), "wb") as out:
                        out.write(bytes(data))
            elif kind == "before":
                if args.dump and data is not None:
                    tag = payload["address"].replace("0x", "")
                    with open(os.path.join(args.dump, f"before_{tag}.bin"), "wb") as out:
                        out.write(bytes(data))
            elif kind == "pkt":
                if args.dump and data is not None:
                    with open(os.path.join(args.dump, f"packet_{payload['size']}.bin"), "wb") as out:
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
    if args.dump:
        print(f"\n{saved['packets']} changed buffer(s) written to {args.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
