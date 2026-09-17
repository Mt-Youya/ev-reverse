"""Check whether the player decodes each packet once, twice, or twice with different bytes.

Every measurement so far assumes the packet read at `h264_decode_frame`'s entry is the packet being
turned into the picture that reaches the screen. If the player instead decodes a segment twice --
once from the bytes on disk and once from a version it produced itself -- then the grey frames we
account for and the real picture on screen come from two different calls, and comparing "our bytes"
with "its picture" was never comparing the same thing.

The test needs no decryption: record the pts, the size and a hash of the whole packet for every call,
then group by pts. One call per pts is the simple world. Two calls per pts with identical hashes is a
player that retries. Two calls per pts with *different* hashes is the answer.

    python decode_calls.py --seconds 30 --out captured/calls.jsonl
"""

import argparse
import hashlib
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

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var pkt = args[3];
    if (pkt.isNull()) return;
    var size, data, pts;
    try {
      size = pkt.add(0x20).readS32();
      data = pkt.add(0x18).readPointer();
      pts = pkt.add(0x08).readS64().toNumber();
      if (size <= 0 || size > 8 * 1024 * 1024 || data.isNull()) return;
      calls += 1;
      var blob = data.readByteArray(size);
      send({ t: 'call', n: calls, at: Date.now() / 1000, pts: pts, size: size,
             address: data.toString() }, blob);
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
    ap.add_argument("--out", default=os.path.join("captured", "calls.jsonl"))
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    handle = open(args.out, "w", encoding="utf-8")

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
            elif payload.get("t") == "stats":
                print(f"[{pid}] calls={payload['calls']}", flush=True)
            elif payload.get("t") == "error":
                print(f"[{pid}] {payload['message']}", flush=True)
            elif payload.get("t") == "call":
                digest = hashlib.md5(bytes(data)).hexdigest() if data is not None else None
                record = {k: payload[k] for k in ("n", "at", "pts", "size", "address")}
                record["md5"] = digest
                record["pid"] = pid
                handle.write(json.dumps(record) + "\n")
                handle.flush()

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
    handle.close()

    records = [json.loads(line) for line in open(args.out, encoding="utf-8")]
    by_pts = {}
    for record in records:
        by_pts.setdefault(record["pts"], []).append(record)
    print(f"\n{len(records)} call(s), {len(by_pts)} distinct pts")
    repeated_same = repeated_diff = 0
    examples = []
    for pts, group in by_pts.items():
        if len(group) < 2:
            continue
        digests = {row["md5"] for row in group}
        if len(digests) == 1:
            repeated_same += 1
        else:
            repeated_diff += 1
            if len(examples) < 8:
                examples.append((pts, [(row["n"], row["size"], (row["md5"] or "")[:10])
                                       for row in group]))
    print(f"pts decoded more than once: {repeated_same + repeated_diff}  "
          f"(identical bytes {repeated_same}, different bytes {repeated_diff})")
    for pts, rows in examples:
        print(f"  pts={pts}  {rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
