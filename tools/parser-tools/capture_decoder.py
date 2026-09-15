"""What the player hands its H.264 decoder, byte for byte.

The offline path reproduces the transport stream exactly -- 100% sync, intact AUD/SPS/PPS, clean
AAC -- and the video still decodes to grey. Audio and video share one ciphertext, so the key and
the mask are right; something else touches the video before the decoder sees it. The DLL names the
suspect in its own strings (`DecryptFilterMgr`, `DecryptFilter@ev`, `release DecryptFilter start!`)
and statically links FFmpeg 4.2.9 (`Lavc58.54.100`), so both ends of that pipeline are in one
process and both can be watched.

This hooks the decoder entry and logs the packet it is given: pointer, size, hash, and the first
bytes. Decrypt the same segment with `evmedia` and the difference between the two is the transform
being looked for. It also logs calls into `DecryptFilter`'s methods, which is the other end.

The entry is `0xB9648`, and finding it took two wrong guesses worth recording. The `AVCodec` struct
for h264 is at `0xBBB2C0` (name and long_name both match), but this build's field order is not
FFmpeg 4.2's, so the code pointer at `+0x80` (`0x266690`) is `update_thread_context` — decompiling it
shows a 0x180-byte context memcpy. What settles it is the call chain up from the decoder's own
error path ("error while decoding MB", `0x265624`): `0x265EE0` (decode_slice) is called from
`0xB9AC4`, which is called from `0xB9648` — a function with no callers at all, which is what a
function-pointer slot looks like. `0xB9648` is the code pointer at struct `+0xB0`.

    python capture_decoder.py --pid <EVPlayer2 pid> --seconds 300
    python capture_decoder.py --follow --seconds 900      # wait for a fresh player

Then play a lesson in the player for half a minute.
"""

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "decoder.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');

function hexAt(address, count) {
  try {
    var bytes = new Uint8Array(ptr(address).readByteArray(count)), out = '';
    for (var i = 0; i < bytes.length; i++) out += ('0' + bytes[i].toString(16)).slice(-2);
    return out;
  } catch (e) { return null; }
}

// h264_decode_frame(AVCodecContext *avctx, void *data, int *got_frame, AVPacket *avpkt).
// AVPacket in FFmpeg 4.2: buf +0x00, pts +0x08, dts +0x10, data +0x18, size +0x20, stream_index +0x24.
Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var packet = args[3];
    if (packet.isNull()) return;
    var size = -1, data = null;
    try {
      size = packet.add(0x20).readS32();
      data = packet.add(0x18).readPointer();
    } catch (e) { return; }
    if (size <= 0 || size > 4 * 1024 * 1024 || data.isNull()) return;
    var head = hexAt(data, 64);
    if (head === null) return;
    send({ t: 'packet', size: size, head: head, stream: packet.add(0x24).readS32() },
         data.readByteArray(Math.min(size, 16384)));
  }
});

// The filter chain the DLL names. Slot 4 and 6 look like the ones with real bodies (392 and 394
// bytes); logging their arguments shows whether the bitstream passes through them on the way in.
[0x19a70, 0x1a2d0, 0x19c20].forEach(function (rva) {
  Interceptor.attach(dll.base.add(rva), {
    onEnter: function (args) {
      send({ t: 'filter', at: rva, a0: args[0].toString(), a1: args[1].toString(),
             a2: args[2].toString() });
    }
  });
});

send({ t: 'ready', base: dll.base.toString() });
"""


def wait_for_player(known):
    print("waiting for an EVPlayer2 process…", flush=True)
    while True:
        listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv"],
                                 capture_output=True, text=True).stdout
        pids = {int(row.split('","')[1]) for row in listing.splitlines()
                if row.startswith('"EVPlayer2.exe"')}
        fresh = pids - known
        if fresh:
            return sorted(fresh)[-1]
        known |= pids
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--follow", action="store_true")
    args = ap.parse_args()

    if not args.pid:
        if not args.follow:
            print("pass --pid, or --follow to wait for a fresh player")
            return 1
        args.pid = wait_for_player(set())

    print(f"attaching pid={args.pid}", flush=True)
    session = frida.attach(args.pid)
    script = session.create_script(JS)
    seen = collections.Counter()
    packets = 0
    handle = open(OUT, "a", encoding="utf-8")

    def on_message(message, data):
        nonlocal packets
        if message.get("type") != "send":
            print(json.dumps(message)[:200], flush=True)
            return
        payload = message["payload"]
        if payload.get("t") == "ready":
            print(f"armed at base {payload['base']}", flush=True)
            return
        if payload.get("t") == "filter":
            key = payload["at"]
            seen[key] += 1
            if seen[key] == 1:
                print(f"FILTER 0x{key:x}  a0={payload['a0']} a1={payload['a1']} a2={payload['a2']}",
                      flush=True)
            handle.write(json.dumps({"at": time.time(), **payload}) + "\n")
            handle.flush()
            return
        if payload.get("t") != "packet":
            return
        packets += 1
        digest = hashlib.sha256(bytes(data)).hexdigest()[:16] if data else None
        record = {"at": time.time(), "index": packets, "size": payload["size"],
                  "head": payload["head"], "sha256_16k": digest}
        handle.write(json.dumps(record) + "\n")
        handle.flush()
        if packets <= 12 or packets % 50 == 0:
            print(f"[{packets:4d}] packet {payload['size']:7d} B  head={payload['head'][:32]}  "
                  f"sha={digest}", flush=True)

    script.on("message", on_message)
    script.load()
    print(f"watching {args.seconds:.0f}s — play a lesson in the player now", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            session.detach()
        except Exception:
            pass
        handle.close()
    print(f"\n{packets} decoder packet(s), {len(seen)} filter entr(y/ies) called")
    for rva, count in seen.most_common():
        print(f"  filter 0x{rva:x}: {count} call(s)")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
