"""Collect segment keys from a live player, continuously, while it plays.

The offline derivation `MD5_hex(tk + filename + "20220507")` is not what this uses, and the reason
is measured rather than theoretical: a file decrypted with a key derived that way comes out as a
structurally perfect transport stream -- 100% sync, clean AAC, parsable SPS/PPS -- whose video
decodes to flat grey (428 h264 errors in a single 900 KB segment), while the same kind of file
decrypted with the key the *player* set up decodes with **zero** errors and yields real frames.
Keys therefore come from `hls_decode` (RVA 0x40AF0), whose argument is an MSVC `std::string`.

This runs for as long as it is given and appends one JSON line per key to
`captured/player_keys.jsonl`, so a later run can build videos from everything seen so far without
depending on this process still being alive.

    python harvest_player.py --pid <pid> --seconds 7200
    python harvest_player.py --follow --seconds 7200
"""

import argparse
import json
import os
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "player_keys.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');

// MSVC std::string: the 16-byte union is first, so size sits at +0x10 and capacity at +0x18; a
// capacity below 16 means the characters are inline. Reading it as a C string returns the bytes of
// the buffer pointer instead, which is how this hook first reported `None` on every call.
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
    if (key && /^[0-9a-f]{32}$/.test(key)) send({ t: 'key', key: key });
  }
});
send({ t: 'armed', base: dll.base.toString() });
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
    args = ap.parse_args()

    if not args.pid:
        if not args.follow:
            print("pass --pid, or --follow to wait for a fresh player")
            return 1
        args.pid = wait_for_player(set())

    known = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                known.add(json.loads(line)["key"])
            except Exception:
                pass
    print(f"{len(known)} key(s) already on file in {OUT}", flush=True)

    session = frida.attach(args.pid)
    script = session.create_script(JS)
    handle = open(OUT, "a", encoding="utf-8")
    fresh = 0

    def on_message(message, data):
        nonlocal fresh
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "armed":
            print(f"armed at {payload['base']} — play a lesson; every key seen is kept", flush=True)
            return
        if payload.get("t") != "key":
            return
        key = payload["key"]
        handle.write(json.dumps({"at": time.time(), "key": key, "known": key in known}) + "\n")
        handle.flush()
        if key not in known:
            known.add(key)
            fresh += 1
            print(f"[{fresh:4d} new / {len(known):4d} total] {key}", flush=True)

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
        handle.close()
    print(f"\n{fresh} new key(s), {len(known)} distinct in total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
