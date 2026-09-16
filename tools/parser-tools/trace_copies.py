"""Trace every copy out of the packet, to find the buffer the decoder really reads.

The patch is not in `h264_decode_frame` (that function is stock, down to its `libavcodec/h264dec.c`
assertion strings), the packet buffer is never rewritten, and each packet is decoded exactly once --
so whatever turns these bytes into a picture happens on a copy made inside the call. FFmpeg does make
one: the NAL payload is unescaped into `H2645Packet.rbsp_buffer` before anything parses it.

Rather than guess that buffer's address, watch the copies happen. `memcpy` in this DLL is a six-byte
thunk, so every call the compiler emitted goes through one address; hook it, keep only the calls whose
source lies inside the packet currently being decoded, and write the destination to disk. Comparing
what lands there against the packet answers the question directly: emulation bytes removed and nothing
else means the payload really is H.264, and anything more than that is the layer we are looking for.

    python trace_copies.py --seconds 40 --dump captured/copies
"""

import argparse
import os
import subprocess
import sys
import time

import frida

MEMCPY_RVA = 0x7F6914        # Ghidra 0x1807f6914 - image base 0x180000000
DECODE_RVA = 0xB9648
BIG = 8192

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var pktBase = null, pktSize = 0, pktPts = 0, copies = 0, kept = 0, calls = 0;

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    pktBase = null;
    var pkt = args[3];
    if (pkt.isNull()) return;
    try {
      var size = pkt.add(0x20).readS32();
      var data = pkt.add(0x18).readPointer();
      if (size < 8192 || size > 8 * 1024 * 1024 || data.isNull()) return;
      pktBase = data;
      pktSize = size;
      pktPts = pkt.add(0x08).readS64().toNumber();
      calls += 1;
      send({ t: 'packet', pts: pktPts, size: size, address: data.toString(), calls: calls });
      if (kept < 3) {
        kept += 1;
        send({ t: 'packet_bytes', pts: pktPts, size: size }, data.readByteArray(size));
      }
    } catch (e) {
      pktBase = null;
    }
  }
});

Interceptor.attach(dll.base.add(0x7F6914), {
  onEnter: function (args) {
    this.report = null;
    if (pktBase === null) return;
    var dst = args[0], src = args[1], size = args[2].toInt32();
    if (size <= 0) return;
    var offset = src.sub(pktBase).toInt32();
    if (offset < 0 || offset >= pktSize) return;         // not out of this packet
    copies += 1;
    // The dump has to happen after the copy, not before: reading the destination on entry returns
    // whatever the buffer held previously, which is how the first version of this reported that
    // every copy was "different" and started with the source bytes.
    this.dst = dst;
    this.size = size;
    this.report = { t: 'copy', pts: pktPts, packet_size: pktSize, src_offset: offset,
                    size: size, dst: dst.toString(), keep: copies <= 6 };
  },
  onLeave: function () {
    if (!this.report) return;
    var blob = null;
    if (this.report.keep) {
      try { blob = this.dst.readByteArray(this.size); } catch (e) {}
    }
    send(this.report, blob || undefined);
    this.report = null;
  }
});

setInterval(function () { send({ t: 'stats', calls: calls, copies: copies }); }, 5000);
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
    packets = {}

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
                print(f"[{pid}] big packets={payload['calls']} copies out of them="
                      f"{payload['copies']}", flush=True)
            elif kind == "packet":
                print(f"[{pid}] packet {payload['size']:,} B pts={payload['pts']} "
                      f"at {payload['address']}", flush=True)
            elif kind == "packet_bytes" and args.dump and data is not None:
                name = os.path.join(args.dump, f"packet_{payload['pts']}_{payload['size']}.bin")
                with open(name, "wb") as out:
                    out.write(bytes(data))
                packets[payload["pts"]] = name
            elif kind == "copy":
                note = ""
                if args.dump and data is not None:
                    tag = payload["dst"][-6:]
                    name = os.path.join(args.dump,
                                        f"copy_{payload['pts']}_off{payload['src_offset']}"
                                        f"_{payload['size']}_at{tag}.bin")
                    with open(name, "wb") as out:
                        out.write(bytes(data))
                    note = f" -> {os.path.basename(name)}"
                print(f"[{pid}] copy of {payload['size']:,} B from packet+{payload['src_offset']:,} "
                      f"to {payload['dst']}{note}", flush=True)

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
