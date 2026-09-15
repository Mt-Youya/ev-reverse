"""Three things at once, so the player's own decryption can be compared with ours.

The offline path reproduces the transport stream -- sync, NAL structure, PES timestamps -- and its
video still decodes to grey, while the audio in the same ciphertext decodes cleanly. That leaves one
question: are the bytes the player hands its H.264 decoder the same bytes `evmedia` produces?

Answering it needs three observations from the *same* playback:
  * which file the player opened (`CreateFileW`),
  * the key it set up (`0x20EC0`) and the key it gave `hls_decode` (`0x40AF0`),
  * the compressed frames it fed the decoder (`0xB9648`, the `AVCodec` decode slot).

With those, the file can be decrypted here using the player's own key and compared byte for byte
with what it decoded. Same bytes means the transform is elsewhere; different bytes means the
difference *is* the transform.

    python capture_playback.py --pid <pid> --seconds 3600     # then play whenever
    python capture_playback.py --follow --seconds 3600
"""

import argparse
import collections
import json
import os
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "playback")
LOG = os.path.join(HERE, "captured", "playback.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');

function cstring(p, max) { try { return p.readUtf8String(max); } catch (e) { return null; } }

// `Module.getExportByName` is not a function in this Frida build, and it fails at the *top* of the
// script -- which silently disarms every hook written after it. That is what made a run against a
// player that was plainly decoding report zero events of every kind.
var create = null;
try {
  create = Module.findExportByName('kernel32.dll', 'CreateFileW')
        || Process.getModuleByName('kernel32.dll').findExportByName('CreateFileW');
} catch (e) { send({ t: 'warn', what: 'CreateFileW', error: String(e) }); }
if (create) Interceptor.attach(create, {
  onEnter: function (args) {
    try {
      var path = args[0].readUtf16String();
      if (path && path.indexOf('.ts') >= 0) send({ t: 'open', path: path });
    } catch (e) {}
  }
});
send({ t: 'armed', what: 'file' });

// 0x20EC0 is the app's own AES key setup: (schedule, const char* key, bits, direction).
Interceptor.attach(dll.base.add(0x20EC0), {
  onEnter: function (args) {
    var key = cstring(args[1], 256);
    if (key && key.length >= 8)
      send({ t: 'key', key: key, bits: args[2].toInt32(), direction: args[3].toInt32(),
             caller: this.returnAddress.sub(dll.base).toInt32() });
  }
});

// hls_decode's AES init takes the key as a std::string reference.
Interceptor.attach(dll.base.add(0x40AF0), {
  onEnter: function (args) {
    var key = null;
    try { key = args[1].readPointer().readUtf8String(64); } catch (e) { key = cstring(args[1], 64); }
    send({ t: 'hls', key: key });
  }
});

// h264_decode_frame: AVPacket has data at +0x18 and size at +0x20.
Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var packet = args[3];
    if (packet.isNull()) return;
    var size = -1, data = null;
    try { size = packet.add(0x20).readS32(); data = packet.add(0x18).readPointer(); } catch (e) { return; }
    if (size <= 0 || size > 4 * 1024 * 1024 || data.isNull()) return;
    send({ t: 'packet', size: size }, data.readByteArray(size));
  }
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
    ap.add_argument("--seconds", type=float, default=3600)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--packets", type=int, default=80, help="how many frames to keep on disk")
    args = ap.parse_args()

    if not args.pid:
        if not args.follow:
            print("pass --pid, or --follow to wait for a fresh player")
            return 1
        args.pid = wait_for_player(set())

    os.makedirs(OUT, exist_ok=True)
    session = frida.attach(args.pid)
    script = session.create_script(JS)
    counts = collections.Counter()
    kept = 0
    log = open(LOG, "a", encoding="utf-8")

    def record(entry):
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.flush()

    def on_message(message, data):
        nonlocal kept
        if message.get("type") != "send":
            return
        payload = message["payload"]
        kind = payload.get("t")
        if kind == "ready":
            print(f"armed at base {payload['base']} — play whenever, the hooks stay on", flush=True)
            return
        counts[kind] += 1
        if kind == "open":
            print(f"OPEN  {payload['path']}", flush=True)
            record({"at": time.time(), **payload})
        elif kind == "key":
            print(f"KEY   caller=0x{payload['caller']:06x} {payload['bits']} bit "
                  f"dir={payload['direction']} {payload['key']!r}", flush=True)
            record({"at": time.time(), **payload})
        elif kind == "hls":
            print(f"HLS   key={payload['key']!r}", flush=True)
            record({"at": time.time(), **payload})
        elif kind == "packet":
            kept += 1
            if kept <= args.packets and data is not None:
                name = f"{kept:03d}_{payload['size']}.bin"
                with open(os.path.join(OUT, name), "wb") as handle:
                    handle.write(bytes(data))
            record({"at": time.time(), "t": "packet", "size": payload["size"], "file":
                    f"{kept:03d}_{payload['size']}.bin" if kept <= args.packets else None})

    script.on("message", on_message)
    script.load()
    print(f"watching {args.seconds:.0f}s", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            session.detach()
        except Exception:
            pass
        log.close()
    print(f"\n{counts['open']} file open(s), {counts['key']} key setup(s), {counts['hls']} hls init(s), "
          f"{counts['packet']} decoder packet(s); {kept} kept under {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
